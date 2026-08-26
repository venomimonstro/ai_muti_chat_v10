from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Avg, Count, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import SupportRequest, User
from apps.ai_registry.models import AIModel, Provider, ReliabilityIncident
from apps.b2b_api.models import APIKey, APIUsage, Organization
from apps.billing.models import (
    BillingReconciliationRun,
    CostAnomaly,
    LedgerEntry,
    MarginPolicyVersion,
    MarkupRuleVersion,
    PriceVersion,
    RequestCost,
    Wallet,
)
from apps.chat.models import Generation
from apps.evals.models import EvalRun, ModelScore
from apps.payments.models import Payment, PaymentFeeVersion, ReconciliationRun, Refund

from .models import (
    AdminAuditEvent,
    BackupRecord,
    ComplianceSignoff,
    FeatureFlag,
    ReleaseRecord,
    SecurityEvent,
    StatusIncident,
)
from .permissions import IsPlatformAdmin
from .services import audit

ZERO = Decimal("0")


def _limit(request, default=100, maximum=500):
    try:
        return min(max(int(request.query_params.get("limit", default)), 1), maximum)
    except (TypeError, ValueError):
        return default


def _money(value):
    return str(value or ZERO)


class AdminAPIView(APIView):
    permission_classes = [IsPlatformAdmin]


class ExecutiveOverviewView(AdminAPIView):
    def get(self, request):
        now = timezone.now()
        day = now - timedelta(hours=24)
        month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        chat_day = Generation.objects.filter(created_at__gte=day)
        api_day = APIUsage.objects.filter(created_at__gte=day)
        chat_month = RequestCost.objects.filter(created_at__gte=month)
        api_month = APIUsage.objects.filter(
            created_at__gte=month, state=APIUsage.State.COMPLETED
        )
        revenue = (chat_month.aggregate(value=Sum("charged_rub"))["value"] or ZERO) + (
            api_month.aggregate(value=Sum("charged_rub"))["value"] or ZERO
        )
        provider_cost = (
            chat_month.aggregate(value=Sum("provider_cost_rub"))["value"] or ZERO
        ) + (api_month.aggregate(value=Sum("provider_cost_rub"))["value"] or ZERO)
        request_count = chat_day.count() + api_day.count()
        error_count = chat_day.filter(state=Generation.State.FAILED).count() + api_day.filter(
            state=APIUsage.State.FAILED
        ).count()
        return Response(
            {
                "users": {
                    "total": User.objects.count(),
                    "active": User.objects.filter(status=User.Status.ACTIVE).count(),
                    "new_24h": User.objects.filter(date_joined__gte=day).count(),
                },
                "organizations": {
                    "total": Organization.objects.count(),
                    "active": Organization.objects.filter(active=True).count(),
                },
                "requests_24h": request_count,
                "errors_24h": error_count,
                "error_rate_percent": round(error_count / request_count * 100, 3)
                if request_count
                else 0,
                "month": {
                    "revenue_rub": _money(revenue),
                    "provider_cost_rub": _money(provider_cost),
                    "gross_profit_rub": _money(revenue - provider_cost),
                },
                "open_incidents": ReliabilityIncident.objects.filter(
                    state=ReliabilityIncident.State.OPEN
                ).count(),
                "open_security_events": SecurityEvent.objects.exclude(
                    status=SecurityEvent.Status.RESOLVED
                ).count(),
                "open_cost_anomalies": CostAnomaly.objects.filter(
                    status=CostAnomaly.Status.OPEN
                ).count(),
                "support_open": SupportRequest.objects.exclude(
                    status=SupportRequest.Status.RESOLVED
                ).count(),
            }
        )


class FinanceControlView(AdminAPIView):
    def get(self, request):
        costs = RequestCost.objects.filter(charged_rub__isnull=False).aggregate(
            revenue=Sum("charged_rub"),
            provider=Sum("provider_cost_rub"),
            profit=Sum("gross_profit_rub"),
            average_margin=Avg("gross_margin_percent"),
        )
        api = APIUsage.objects.filter(state=APIUsage.State.COMPLETED).aggregate(
            revenue=Sum("charged_rub"), provider=Sum("provider_cost_rub")
        )
        payments = Payment.objects.filter(status=Payment.Status.SUCCEEDED).aggregate(
            gross=Sum("amount_rub"), count=Count("id")
        )
        refunds = Refund.objects.filter(status=Refund.Status.SUCCEEDED).aggregate(
            total=Sum("amount_rub"), count=Count("id")
        )
        wallets = Wallet.objects.aggregate(
            available=Sum("available_rub"), reserved=Sum("reserved_rub")
        )
        revenue = (costs["revenue"] or ZERO) + (api["revenue"] or ZERO)
        provider_cost = (costs["provider"] or ZERO) + (api["provider"] or ZERO)
        return Response(
            {
                "usage_revenue_rub": _money(revenue),
                "provider_cost_rub": _money(provider_cost),
                "gross_profit_rub": _money(revenue - provider_cost),
                "average_chat_margin_percent": _money(costs["average_margin"]),
                "payments": {
                    "succeeded_count": payments["count"],
                    "gross_rub": _money(payments["gross"]),
                    "refund_count": refunds["count"],
                    "refund_rub": _money(refunds["total"]),
                },
                "liability": {
                    "available_rub": _money(wallets["available"]),
                    "reserved_rub": _money(wallets["reserved"]),
                },
                "last_billing_reconciliation": self._billing_reconciliation(),
                "last_payment_reconciliation": self._payment_reconciliation(),
            }
        )

    def _billing_reconciliation(self):
        item = BillingReconciliationRun.objects.first()
        return (
            {
                "id": item.id,
                "status": item.status,
                "discrepancies": item.discrepancy_count,
                "finished_at": item.finished_at,
            }
            if item
            else None
        )

    def _payment_reconciliation(self):
        item = ReconciliationRun.objects.order_by("-started_at").first()
        return (
            {
                "id": item.id,
                "status": item.status,
                "errors": item.error_count,
                "finished_at": item.finished_at,
            }
            if item
            else None
        )


class PaymentInspectorView(AdminAPIView):
    def get(self, request):
        payments = Payment.objects.select_related("user").order_by("-created_at")
        if request.query_params.get("status"):
            payments = payments.filter(status=request.query_params["status"])
        return Response(
            {
                "fees": [
                    {
                        "id": item.id,
                        "provider": item.provider,
                        "payment_method": item.payment_method,
                        "percent": item.percent,
                        "fixed_rub": item.fixed_rub,
                        "active": item.active,
                        "effective_from": item.effective_from,
                    }
                    for item in PaymentFeeVersion.objects.order_by("-effective_from")[:100]
                ],
                "payments": [
                    {
                        "id": item.id,
                        "user_id": item.user_id,
                        "user_email": item.user.email,
                        "provider": item.provider,
                        "provider_payment_id": item.provider_payment_id,
                        "amount_rub": item.amount_rub,
                        "status": item.status,
                        "receipt_status": item.receipt_status,
                        "last_error": item.last_error,
                        "created_at": item.created_at,
                    }
                    for item in payments[: _limit(request)]
                ],
                "refunds": [
                    {
                        "id": item.id,
                        "payment_id": item.payment_id,
                        "amount_rub": item.amount_rub,
                        "status": item.status,
                        "created_at": item.created_at,
                    }
                    for item in Refund.objects.order_by("-created_at")[: _limit(request)]
                ],
            }
        )


class LedgerInspectorView(AdminAPIView):
    def get(self, request):
        wallets = Wallet.objects.select_related("user").order_by("user__email")
        user_id = request.query_params.get("user_id")
        if user_id:
            wallets = wallets.filter(user_id=user_id)
        data = [
            {
                "id": item.id,
                "user_id": item.user_id,
                "email": item.user.email,
                "available_rub": item.available_rub,
                "reserved_rub": item.reserved_rub,
                "paid_rub": item.paid_rub,
                "promo_rub": item.promo_rub,
                "updated_at": item.updated_at,
            }
            for item in wallets[: _limit(request)]
        ]
        entries = []
        if user_id:
            entries = [
                {
                    "id": item.id,
                    "kind": item.kind,
                    "amount_rub": item.amount_rub,
                    "available_delta_rub": item.available_delta_rub,
                    "reserved_delta_rub": item.reserved_delta_rub,
                    "source_type": item.source_type,
                    "source_id": item.source_id,
                    "created_at": item.created_at,
                }
                for item in LedgerEntry.objects.filter(wallet__user_id=user_id).order_by(
                    "-created_at"
                )[: _limit(request)]
            ]
        return Response({"wallets": data, "entries": entries})


class PricingControlView(AdminAPIView):
    def get(self, request):
        policy = MarginPolicyVersion.objects.filter(active=True).first()
        return Response(
            {
                "margin_policy": {
                    "id": policy.id,
                    "minimum_gross_margin_percent": policy.minimum_gross_margin_percent,
                    "anomaly_cost_deviation_percent": policy.anomaly_cost_deviation_percent,
                    "effective_from": policy.effective_from,
                }
                if policy
                else None,
                "active_prices": [
                    {
                        "id": item.id,
                        "model": item.model_slug,
                        "currency": item.provider_currency,
                        "input_price_per_million": item.input_price_per_million,
                        "output_price_per_million": item.output_price_per_million,
                        "markup_percent": item.markup_percent,
                        "effective_from": item.effective_from,
                    }
                    for item in PriceVersion.objects.filter(active=True).order_by("model_slug")
                ],
                "markup_rules": [
                    {
                        "id": item.id,
                        "scope_type": item.scope_type,
                        "scope_key": item.scope_key,
                        "markup_percent": item.markup_percent,
                        "price_multiplier": item.price_multiplier,
                        "effective_from": item.effective_from,
                    }
                    for item in MarkupRuleVersion.objects.filter(active=True)[:200]
                ],
                "open_anomalies": [
                    {
                        "id": item.id,
                        "kind": item.kind,
                        "severity": item.severity,
                        "model": item.model_slug,
                        "provider": item.provider_slug,
                        "expected_rub": item.expected_rub,
                        "actual_rub": item.actual_rub,
                        "created_at": item.created_at,
                    }
                    for item in CostAnomaly.objects.filter(status=CostAnomaly.Status.OPEN)[:100]
                ],
            }
        )


class QualityControlView(AdminAPIView):
    def get(self, request):
        return Response(
            {
                "runs": [
                    {
                        "id": item.id,
                        "model": item.model.slug,
                        "dataset": item.dataset_version,
                        "state": item.state,
                        "gate": item.gate_status,
                        "average_score": item.average_score,
                        "hallucination_rate": item.hallucination_rate,
                        "error_count": item.error_count,
                        "cost_rub": item.total_cost_rub,
                        "started_at": item.started_at,
                    }
                    for item in EvalRun.objects.select_related("model")[: _limit(request, 30)]
                ],
                "scores": [
                    {
                        "model": item.model.slug,
                        "taxonomy": item.taxonomy,
                        "score": item.score,
                        "case_count": item.case_count,
                        "eligible_for_promotion": item.eligible_for_promotion,
                        "created_at": item.created_at,
                    }
                    for item in ModelScore.objects.select_related("model")[:200]
                ],
            }
        )


class IncidentControlView(AdminAPIView):
    def get(self, request):
        return Response(
            {
                "provider_incidents": [
                    {
                        "id": item.id,
                        "correlation_id": item.correlation_id,
                        "provider": item.provider.slug,
                        "state": item.state,
                        "error_code": item.error_code,
                        "opened_at": item.opened_at,
                        "recovered_at": item.recovered_at,
                    }
                    for item in ReliabilityIncident.objects.select_related("provider")[:100]
                ],
                "cost_anomalies": [
                    {
                        "id": item.id,
                        "kind": item.kind,
                        "status": item.status,
                        "severity": item.severity,
                        "created_at": item.created_at,
                    }
                    for item in CostAnomaly.objects.all()[:100]
                ],
                "security_events": [
                    {
                        "id": item.id,
                        "category": item.category,
                        "severity": item.severity,
                        "status": item.status,
                        "summary": item.summary,
                        "created_at": item.created_at,
                    }
                    for item in SecurityEvent.objects.all()[:100]
                ],
            }
        )


class ProviderControlView(AdminAPIView):
    def get(self, request):
        return Response(
            [
                {
                    "id": provider.id,
                    "slug": provider.slug,
                    "name": provider.name,
                    "enabled": provider.enabled,
                    "emergency_disabled": provider.emergency_disabled,
                    "health_state": provider.health_state,
                    "last_latency_ms": provider.last_latency_ms,
                    "models": [
                        {
                            "id": model.id,
                            "slug": model.slug,
                            "enabled": model.enabled,
                            "current_version": model.current_version.version
                            if model.current_version
                            else None,
                        }
                        for model in provider.models.all()
                    ],
                }
                for provider in Provider.objects.prefetch_related("models__current_version")
            ]
        )


class ProviderBulkActionView(AdminAPIView):
    @transaction.atomic
    def post(self, request):
        target = request.data.get("target")
        action = request.data.get("action")
        ids = request.data.get("ids")
        if target not in {"providers", "models"} or not isinstance(ids, list) or not ids:
            return Response({"detail": "target and non-empty ids are required"}, status=400)
        if target == "models":
            if action not in {"enable", "disable"}:
                return Response({"detail": "Unsupported model action"}, status=400)
            queryset = AIModel.objects.filter(id__in=ids)
            count = queryset.update(enabled=action == "enable")
        else:
            queryset = Provider.objects.filter(id__in=ids)
            if action in {"enable", "disable"}:
                count = queryset.update(enabled=action == "enable")
            elif action in {"emergency_disable", "emergency_enable"}:
                count = queryset.update(emergency_disabled=action == "emergency_disable")
            else:
                return Response({"detail": "Unsupported provider action"}, status=400)
        audit(request, f"{target}.{action}", target, metadata={"ids": ids, "count": count})
        return Response({"updated": count})


class RequestInspectorView(AdminAPIView):
    def get(self, request):
        generations = Generation.objects.select_related(
            "user_message__conversation__owner"
        ).order_by("-created_at")
        api_usage = APIUsage.objects.select_related("organization", "api_key", "model")
        for param in ("state", "provider_slug", "routed_model"):
            if request.query_params.get(param):
                generations = generations.filter(**{param: request.query_params[param]})
        if request.query_params.get("state"):
            api_usage = api_usage.filter(state=request.query_params["state"])
        return Response(
            {
                "chat": [
                    {
                        "id": item.id,
                        "correlation_id": item.correlation_id,
                        "user_id": item.user_message.conversation.owner_id,
                        "state": item.state,
                        "model": item.routed_model or item.model,
                        "provider": item.provider_slug,
                        "input_tokens": item.input_tokens,
                        "output_tokens": item.output_tokens,
                        "cost_rub": item.actual_cost_rub,
                        "error_code": item.error_code,
                        "created_at": item.created_at,
                        "completed_at": item.completed_at,
                    }
                    for item in generations[: _limit(request)]
                ],
                "b2b": [
                    {
                        "id": item.id,
                        "response_id": item.response_id,
                        "organization": item.organization.slug,
                        "key_prefix": item.api_key.prefix,
                        "state": item.state,
                        "model": item.model.slug,
                        "prompt_tokens": item.prompt_tokens,
                        "completion_tokens": item.completion_tokens,
                        "charged_rub": item.charged_rub,
                        "latency_ms": item.latency_ms,
                        "error_code": item.error_code,
                        "created_at": item.created_at,
                    }
                    for item in api_usage[: _limit(request)]
                ],
            }
        )


class UserOrganizationView(AdminAPIView):
    def get(self, request):
        users = User.objects.order_by("-date_joined")
        query = request.query_params.get("query")
        if query:
            users = users.filter(email__icontains=query)
        return Response(
            {
                "users": [
                    {
                        "id": item.id,
                        "email": item.email,
                        "username": item.username,
                        "role": item.role,
                        "status": item.status,
                        "is_staff": item.is_staff,
                        "date_joined": item.date_joined,
                    }
                    for item in users[: _limit(request)]
                ],
                "organizations": [
                    {
                        "id": item.id,
                        "name": item.name,
                        "slug": item.slug,
                        "active": item.active,
                        "billing_user_id": item.billing_user_id,
                        "monthly_limit_rub": item.monthly_limit_rub,
                        "members": item.memberships.count(),
                        "active_keys": item.api_keys.filter(revoked_at__isnull=True).count(),
                        "created_at": item.created_at,
                    }
                    for item in Organization.objects.select_related("billing_user").prefetch_related(
                        "memberships", "api_keys"
                    )[: _limit(request)]
                ],
            }
        )


class SecurityEventView(AdminAPIView):
    def get(self, request):
        queryset = SecurityEvent.objects.select_related("user", "created_by", "resolved_by")
        if request.query_params.get("status"):
            queryset = queryset.filter(status=request.query_params["status"])
        return Response([self._serialize(item) for item in queryset[: _limit(request)]])

    def post(self, request):
        if not request.data.get("category") or not request.data.get("summary"):
            return Response({"detail": "category and summary are required"}, status=400)
        user = None
        if request.data.get("user_id"):
            user = get_object_or_404(User, pk=request.data["user_id"])
        severity = request.data.get("severity", SecurityEvent.Severity.WARNING)
        if severity not in SecurityEvent.Severity.values:
            return Response({"detail": "Invalid severity"}, status=400)
        event = SecurityEvent.objects.create(
            category=request.data["category"],
            summary=request.data["summary"],
            severity=severity,
            user=user,
            ip_address=request.data.get("ip_address") or None,
            details=request.data.get("details") or {},
            created_by=request.user,
        )
        audit(request, "security_event.created", "security_event", event.id)
        return Response(self._serialize(event), status=status.HTTP_201_CREATED)

    def _serialize(self, item):
        return {
            "id": item.id,
            "category": item.category,
            "severity": item.severity,
            "status": item.status,
            "user_id": item.user_id,
            "ip_address": item.ip_address,
            "summary": item.summary,
            "details": item.details,
            "created_by": item.created_by_id,
            "resolved_by": item.resolved_by_id,
            "created_at": item.created_at,
            "resolved_at": item.resolved_at,
        }


class SecurityEventActionView(AdminAPIView):
    @transaction.atomic
    def post(self, request, event_id):
        event = get_object_or_404(SecurityEvent.objects.select_for_update(), pk=event_id)
        action = request.data.get("action", "resolve")
        if action not in {"investigate", "resolve", "contain"}:
            return Response({"detail": "Invalid action"}, status=400)
        if action == "investigate":
            event.status = SecurityEvent.Status.INVESTIGATING
            fields = ["status"]
        else:
            event.status = SecurityEvent.Status.RESOLVED
            event.resolved_by = request.user
            event.resolved_at = timezone.now()
            fields = ["status", "resolved_by", "resolved_at"]
            if action == "contain" and event.user_id:
                User.objects.filter(pk=event.user_id).update(
                    status=User.Status.BLOCKED, is_active=False
                )
                APIKey.objects.filter(
                    organization__billing_user_id=event.user_id, revoked_at__isnull=True
                ).update(revoked_at=timezone.now())
        event.save(update_fields=fields)
        audit(request, f"security_event.{action}", "security_event", event.id)
        return Response({"id": event.id, "status": event.status})


class ReleaseView(AdminAPIView):
    def get(self, request):
        return Response([self._serialize(item) for item in ReleaseRecord.objects.all()[:100]])

    def post(self, request):
        if not request.data.get("version") or not request.data.get("commit_sha"):
            return Response({"detail": "version and commit_sha are required"}, status=400)
        release = ReleaseRecord(
            version=request.data["version"],
            commit_sha=request.data["commit_sha"],
            environment=request.data.get("environment", "production"),
            notes=request.data.get("notes", ""),
            created_by=request.user,
        )
        release.full_clean()
        release.save()
        audit(request, "release.created", "release", release.id, {"version": release.version})
        return Response(self._serialize(release), status=status.HTTP_201_CREATED)

    def _serialize(self, item):
        return {
            "id": item.id,
            "version": item.version,
            "commit_sha": item.commit_sha,
            "environment": item.environment,
            "state": item.state,
            "rollout_percent": item.rollout_percent,
            "allow_user_ids": item.allow_user_ids,
            "health_snapshot": item.health_snapshot,
            "notes": item.notes,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }


class ReleaseRolloutView(AdminAPIView):
    TRANSITIONS = {
        ReleaseRecord.State.DRAFT: {ReleaseRecord.State.CANARY},
        ReleaseRecord.State.CANARY: {
            ReleaseRecord.State.ROLLING,
            ReleaseRecord.State.STABLE,
            ReleaseRecord.State.ROLLED_BACK,
        },
        ReleaseRecord.State.ROLLING: {
            ReleaseRecord.State.STABLE,
            ReleaseRecord.State.ROLLED_BACK,
        },
        ReleaseRecord.State.STABLE: {ReleaseRecord.State.ROLLED_BACK},
        ReleaseRecord.State.ROLLED_BACK: set(),
    }

    @transaction.atomic
    def post(self, request, release_id):
        release = get_object_or_404(ReleaseRecord.objects.select_for_update(), pk=release_id)
        new_state = request.data.get("state")
        if new_state not in self.TRANSITIONS[release.state]:
            return Response({"detail": "Invalid release transition"}, status=409)
        percent = request.data.get("rollout_percent")
        if new_state == ReleaseRecord.State.CANARY:
            percent = int(percent or 5)
            valid = 1 <= percent <= 10
        elif new_state == ReleaseRecord.State.ROLLING:
            percent = int(percent or 25)
            valid = 1 <= percent < 100
        elif new_state == ReleaseRecord.State.STABLE:
            percent, valid = 100, True
        else:
            percent, valid = 0, True
        if not valid:
            return Response({"detail": "Invalid rollout percent for state"}, status=400)
        release.state = new_state
        release.rollout_percent = percent
        release.allow_user_ids = request.data.get("allow_user_ids", release.allow_user_ids)
        release.health_snapshot = request.data.get(
            "health_snapshot", release.health_snapshot
        )
        release.full_clean()
        release.save()
        audit(
            request,
            f"release.{new_state}",
            "release",
            release.id,
            {"rollout_percent": percent},
        )
        return Response(
            {"id": release.id, "state": release.state, "rollout_percent": percent}
        )


class BackupView(AdminAPIView):
    def get(self, request):
        return Response([self._serialize(item) for item in BackupRecord.objects.all()[:100]])

    def post(self, request):
        kind = request.data.get("kind")
        if kind not in BackupRecord.Kind.values:
            return Response({"detail": "Invalid backup kind"}, status=400)
        backup = BackupRecord.objects.create(
            kind=kind, notes=request.data.get("notes", ""), requested_by=request.user
        )
        audit(request, "backup.requested", "backup", backup.id, {"kind": kind})
        return Response(self._serialize(backup), status=status.HTTP_201_CREATED)

    def _serialize(self, item):
        return {
            "id": item.id,
            "kind": item.kind,
            "status": item.status,
            "storage_reference": item.storage_reference,
            "size_bytes": item.size_bytes,
            "checksum_sha256": item.checksum_sha256,
            "notes": item.notes,
            "started_at": item.started_at,
            "completed_at": item.completed_at,
            "verified_at": item.verified_at,
            "restored_at": item.restored_at,
            "created_at": item.created_at,
        }


class BackupActionView(AdminAPIView):
    @transaction.atomic
    def post(self, request, backup_id):
        backup = get_object_or_404(BackupRecord.objects.select_for_update(), pk=backup_id)
        action = request.data.get("action")
        now = timezone.now()
        if action == "start" and backup.status == BackupRecord.Status.REQUESTED:
            backup.status = BackupRecord.Status.RUNNING
            backup.started_at = now
            fields = ["status", "started_at"]
        elif action == "complete" and backup.status == BackupRecord.Status.RUNNING:
            checksum = request.data.get("checksum_sha256", "")
            storage = request.data.get("storage_reference", "")
            if len(checksum) != 64 or not storage:
                return Response({"detail": "storage_reference and SHA-256 are required"}, status=400)
            backup.status = BackupRecord.Status.SUCCEEDED
            backup.storage_reference = storage
            backup.checksum_sha256 = checksum
            backup.size_bytes = request.data.get("size_bytes")
            backup.completed_at = now
            fields = [
                "status", "storage_reference", "checksum_sha256", "size_bytes", "completed_at"
            ]
        elif action == "fail" and backup.status in {
            BackupRecord.Status.REQUESTED,
            BackupRecord.Status.RUNNING,
        }:
            backup.status = BackupRecord.Status.FAILED
            backup.completed_at = now
            backup.notes = request.data.get("notes", backup.notes)
            fields = ["status", "completed_at", "notes"]
        elif action == "verify" and backup.status == BackupRecord.Status.SUCCEEDED:
            backup.status = BackupRecord.Status.VERIFIED
            backup.verified_at = now
            fields = ["status", "verified_at"]
        elif action == "restore_drill" and backup.status == BackupRecord.Status.VERIFIED:
            backup.status = BackupRecord.Status.RESTORED
            backup.restored_at = now
            fields = ["status", "restored_at"]
        else:
            return Response({"detail": "Invalid backup transition"}, status=409)
        backup.save(update_fields=fields)
        audit(request, f"backup.{action}", "backup", backup.id)
        return Response({"id": backup.id, "status": backup.status})


class SupportControlView(AdminAPIView):
    def get(self, request):
        queryset = SupportRequest.objects.select_related("user").order_by("-created_at")
        if request.query_params.get("status"):
            queryset = queryset.filter(status=request.query_params["status"])
        return Response(
            [
                {
                    "id": item.id,
                    "user_id": item.user_id,
                    "user_email": item.user.email,
                    "subject": item.subject,
                    "message": item.message,
                    "status": item.status,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
                for item in queryset[: _limit(request)]
            ]
        )


class SupportStatusView(AdminAPIView):
    def post(self, request, support_id):
        item = get_object_or_404(SupportRequest, pk=support_id)
        new_status = request.data.get("status")
        if new_status not in SupportRequest.Status.values:
            return Response({"detail": "Invalid support status"}, status=400)
        item.status = new_status
        item.save(update_fields=["status", "updated_at"])
        audit(request, "support.status_changed", "support_request", item.id, {"status": new_status})
        return Response({"id": item.id, "status": item.status})


class FeatureFlagView(AdminAPIView):
    def get(self, request):
        return Response([self._serialize(item) for item in FeatureFlag.objects.all()])

    def post(self, request):
        key = request.data.get("key")
        if not key:
            return Response({"detail": "key is required"}, status=400)
        flag = FeatureFlag(
            key=key,
            description=request.data.get("description", ""),
            enabled=bool(request.data.get("enabled", False)),
            rollout_percent=int(request.data.get("rollout_percent", 0)),
            allow_user_ids=request.data.get("allow_user_ids") or [],
            deny_user_ids=request.data.get("deny_user_ids") or [],
            updated_by=request.user,
        )
        flag.full_clean()
        flag.save()
        audit(request, "feature_flag.created", "feature_flag", flag.key)
        return Response(self._serialize(flag), status=status.HTTP_201_CREATED)

    def _serialize(self, item):
        return {
            "key": item.key,
            "description": item.description,
            "enabled": item.enabled,
            "rollout_percent": item.rollout_percent,
            "allow_user_ids": item.allow_user_ids,
            "deny_user_ids": item.deny_user_ids,
            "updated_by": item.updated_by_id,
            "updated_at": item.updated_at,
        }


class FeatureFlagDetailView(FeatureFlagView):
    def patch(self, request, key):
        flag = get_object_or_404(FeatureFlag, pk=key)
        for field in (
            "description",
            "enabled",
            "rollout_percent",
            "allow_user_ids",
            "deny_user_ids",
        ):
            if field in request.data:
                setattr(flag, field, request.data[field])
        flag.updated_by = request.user
        flag.full_clean()
        flag.save()
        audit(request, "feature_flag.updated", "feature_flag", flag.key)
        return Response(self._serialize(flag))


class AuditView(AdminAPIView):
    def get(self, request):
        queryset = AdminAuditEvent.objects.select_related("actor")
        if request.query_params.get("action"):
            queryset = queryset.filter(action=request.query_params["action"])
        return Response(
            [
                {
                    "id": item.id,
                    "actor_id": item.actor_id,
                    "actor_email": item.actor.email,
                    "action": item.action,
                    "target_type": item.target_type,
                    "target_id": item.target_id,
                    "metadata": item.metadata,
                    "request_ip": item.request_ip,
                    "created_at": item.created_at,
                }
                for item in queryset[: _limit(request, 200)]
            ]
        )


class ComplianceSignoffView(AdminAPIView):
    REQUIRED = {
        "entity-tax-regime": "Юрлицо и налоговый режим",
        "wallet-fiscalization": "Фискализация пополнения баланса",
        "receipt-refund-flow": "Чеки оплаты и возврата",
        "privacy-data-flow": "Privacy и карта потоков данных",
        "provider-commercial-terms": "Коммерческие условия AI-провайдеров",
        "public-legal-documents": "Оферта, privacy policy и правила возврата",
        "admin-mfa": "MFA административных аккаунтов",
    }

    def get(self, request):
        existing = {item.key: item for item in ComplianceSignoff.objects.all()}
        return Response(
            [
                self._serialize(
                    existing.get(key)
                    or ComplianceSignoff(key=key, title=title)
                )
                for key, title in self.REQUIRED.items()
            ]
        )

    def post(self, request):
        key = request.data.get("key")
        if key not in self.REQUIRED:
            return Response({"detail": "Unknown sign-off key"}, status=400)
        signoff_status = request.data.get("status", ComplianceSignoff.Status.PENDING)
        if signoff_status not in ComplianceSignoff.Status.values:
            return Response({"detail": "Invalid sign-off status"}, status=400)
        evidence = request.data.get("evidence_reference", "")
        if signoff_status == ComplianceSignoff.Status.APPROVED and not evidence:
            return Response({"detail": "Approved sign-off requires evidence"}, status=400)
        item, _created = ComplianceSignoff.objects.get_or_create(
            key=key, defaults={"title": self.REQUIRED[key]}
        )
        item.title = self.REQUIRED[key]
        item.status = signoff_status
        item.evidence_reference = evidence
        item.notes = request.data.get("notes", "")
        item.reviewed_by = request.user if signoff_status != item.Status.PENDING else None
        item.reviewed_at = timezone.now() if signoff_status != item.Status.PENDING else None
        item.save()
        audit(
            request,
            "compliance_signoff.updated",
            "compliance_signoff",
            item.key,
            {"status": item.status, "evidence_reference": evidence},
        )
        return Response(self._serialize(item))

    def _serialize(self, item):
        return {
            "key": item.key,
            "title": item.title,
            "status": item.status,
            "evidence_reference": item.evidence_reference,
            "notes": item.notes,
            "reviewed_by": item.reviewed_by_id,
            "reviewed_at": item.reviewed_at,
        }


class StatusIncidentControlView(AdminAPIView):
    def get(self, request):
        return Response([self._serialize(item) for item in StatusIncident.objects.all()[:100]])

    def post(self, request):
        required = ("title", "message", "impact")
        if any(not request.data.get(field) for field in required):
            return Response({"detail": "title, message and impact are required"}, status=400)
        if request.data["impact"] not in StatusIncident.Impact.values:
            return Response({"detail": "Invalid impact"}, status=400)
        item = StatusIncident.objects.create(
            title=request.data["title"],
            message=request.data["message"],
            impact=request.data["impact"],
            affected_components=request.data.get("affected_components") or [],
            created_by=request.user,
        )
        audit(request, "status_incident.created", "status_incident", item.id)
        return Response(self._serialize(item), status=status.HTTP_201_CREATED)

    def _serialize(self, item):
        return {
            "id": item.id,
            "title": item.title,
            "message": item.message,
            "impact": item.impact,
            "state": item.state,
            "affected_components": item.affected_components,
            "started_at": item.started_at,
            "updated_at": item.updated_at,
            "resolved_at": item.resolved_at,
        }


class StatusIncidentUpdateView(StatusIncidentControlView):
    def post(self, request, incident_id):
        item = get_object_or_404(StatusIncident, pk=incident_id)
        new_state = request.data.get("state")
        if new_state not in StatusIncident.State.values:
            return Response({"detail": "Invalid incident state"}, status=400)
        if item.state == StatusIncident.State.RESOLVED:
            return Response({"detail": "Resolved incident is immutable"}, status=409)
        item.state = new_state
        if request.data.get("message"):
            item.message = request.data["message"]
        item.resolved_at = (
            timezone.now() if new_state == StatusIncident.State.RESOLVED else None
        )
        item.save(update_fields=["state", "message", "resolved_at", "updated_at"])
        audit(
            request,
            "status_incident.updated",
            "status_incident",
            item.id,
            {"state": item.state},
        )
        return Response(self._serialize(item))
