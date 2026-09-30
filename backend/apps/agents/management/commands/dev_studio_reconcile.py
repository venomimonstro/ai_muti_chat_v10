from types import SimpleNamespace

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.agents.accounting import settle_agent_provider_spend
from apps.agents.models import AgentRun, AgentStepRun, AgentTeam
from apps.ai_registry.models import AIModel
from apps.billing.models import BalanceReservation, PriceVersion
from apps.billing.pricing import quote, require_margin
from apps.billing.services import settle
from apps.procurement.models import ProviderSpendReservation


def _interrupted_attempt(step):
    attempts = (step.output_payload or {}).get("model_attempts") or []
    if not isinstance(attempts, list):
        return None
    for item in reversed(attempts):
        if isinstance(item, dict) and item.get("status") == "settlement_interrupted":
            return item
    return None


class Command(BaseCommand):
    help = "Inspect or reconcile a Dev Studio provider-delivered but unsettled stage"

    def add_arguments(self, parser):
        parser.add_argument("--run-id", required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        run_id = str(options["run_id"]).strip()
        apply = bool(options["apply"])
        try:
            run = AgentRun.objects.select_related("team", "owner").get(pk=run_id)
        except AgentRun.DoesNotExist as exc:
            raise CommandError(f"Dev run not found: {run_id}") from exc
        if not run.team_id or run.team.kind != AgentTeam.Kind.DEVELOPMENT:
            raise CommandError("Run is not a Dev Studio run")
        if run.error_code not in {"dev_settlement_interrupted", "dev_settlement_reconciled"}:
            raise CommandError(f"Run error_code={run.error_code or '-'} is not an interrupted Dev settlement")

        step = None
        attempt = None
        for candidate in run.steps.order_by("-sequence", "-created_at"):
            current = _interrupted_attempt(candidate)
            if current:
                step = candidate
                attempt = current
                break
        if step is None or attempt is None:
            raise CommandError("Interrupted settlement evidence is missing")

        customer_id = str(attempt.get("customer_reservation_id") or "").strip()
        provider_id = str(attempt.get("provider_reservation_id") or "").strip()
        price_id = str(attempt.get("price_version_id") or "").strip()
        model_slug = str(attempt.get("model") or "").strip()
        input_tokens = int(attempt.get("input_tokens") or 0)
        output_tokens = int(attempt.get("output_tokens") or 0)
        provider_request_id = str(attempt.get("provider_request_id") or "")[:200]
        if not all([customer_id, provider_id, price_id, model_slug]) or input_tokens < 0 or output_tokens < 0:
            raise CommandError("Reconciliation evidence is incomplete")

        customer = BalanceReservation.objects.select_related("wallet").filter(pk=customer_id).first()
        provider = ProviderSpendReservation.objects.select_related("account__provider").filter(pk=provider_id).first()
        price = PriceVersion.objects.filter(pk=price_id).first()
        model = AIModel.objects.select_related("provider").filter(slug=model_slug).first()
        if customer is None or provider is None or price is None or model is None:
            raise CommandError("Referenced reservation/model/price evidence no longer resolves")
        if customer.wallet.user_id != run.owner_id:
            raise CommandError("Customer reservation belongs to another owner")
        if not customer.idempotency_key.startswith(f"agent-run:{run.id}:step:"):
            raise CommandError("Customer reservation does not belong to this Dev run")
        if not provider.source_key.startswith(f"agent:{run.id}:step:"):
            raise CommandError("Provider reservation does not belong to this Dev run")
        if provider.account.provider_id != model.provider_id:
            raise CommandError("Provider reservation/model mismatch")
        if price.model_slug != model.slug:
            raise CommandError("PriceVersion/model mismatch")

        self.stdout.write("=== DEV STUDIO SETTLEMENT RECONCILIATION ===")
        self.stdout.write(
            f"run={run.id} step={step.id} model={model.slug} provider={model.provider.slug} "
            f"customer_state={customer.state} provider_state={provider.state} "
            f"tokens={input_tokens}/{output_tokens}"
        )

        if customer.state != BalanceReservation.State.ACTIVE or provider.state != ProviderSpendReservation.State.ACTIVE:
            if customer.state != BalanceReservation.State.ACTIVE and provider.state != ProviderSpendReservation.State.ACTIVE:
                self.stdout.write(self.style.SUCCESS("DEV_STUDIO_SETTLEMENT_ALREADY_CLOSED"))
                return
            raise CommandError("Only one side of settlement is active; manual finance review required")

        actual_quote = require_margin(
            quote(
                price,
                max(1, input_tokens),
                max(1, output_tokens),
                provider_slug=model.provider.slug,
                model_slug=model.slug,
                operation_type="agent",
            )
        )
        actual = min(actual_quote.user_charge_rub, customer.amount_rub)
        self.stdout.write(f"reconciled_charge_rub={actual}")
        if not apply:
            raise CommandError("Settlement requires --apply after reviewing evidence")

        result = SimpleNamespace(
            provider_request_id=provider_request_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        with transaction.atomic():
            settle_agent_provider_spend(
                reservation=provider,
                model=model,
                result=result,
                actual_quote=actual_quote,
                source_id=str(provider.source_key).removeprefix("agent:"),
                customer_charge=actual,
            )
            settle(customer.id, actual)

            payload = dict(step.output_payload or {})
            rows = list(payload.get("model_attempts") or [])
            for row in rows:
                if isinstance(row, dict) and row.get("status") == "settlement_interrupted" and str(row.get("customer_reservation_id") or "") == customer_id:
                    row["status"] = "reconciled"
                    row["actual_charge_rub"] = str(actual)
                    row["reconciled_at"] = timezone.now().isoformat()
            payload["model_attempts"] = rows
            step.output_payload = payload
            step.save(update_fields=["output_payload"])

            run.error_code = "dev_settlement_reconciled"
            run.error_message = (
                "Финансовый settlement восстановлен по provider usage evidence. "
                "Выполнение задачи автоматически не продолжалось; создайте новый Dev Run."
            )
            run.save(update_fields=["error_code", "error_message", "updated_at"])

        self.stdout.write(self.style.SUCCESS("DEV_STUDIO_SETTLEMENT_RECONCILED"))
