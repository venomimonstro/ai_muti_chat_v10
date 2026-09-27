import json

import certifi
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.ai_registry.adapters import ProviderError
from apps.ai_registry.gigachat_adapter import (
    DEFAULT_API_BASE_URL,
    GigaChatAPIAdapter,
    normalize_base_url,
    normalize_model_id,
)
from apps.ai_registry.models import AIModel, Provider, ProviderApiKey


class Command(BaseCommand):
    help = "Safely diagnose GigaChat OAuth, model access and streaming without printing secrets."

    def add_arguments(self, parser):
        parser.add_argument("--repair", action="store_true", help="Repair legacy base URL/model aliases and reset stale circuit state after a successful probe")
        parser.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable report")

    def handle(self, *args, **options):
        repair = bool(options["repair"])
        provider = Provider.objects.filter(slug="gigachat").first()
        if provider is None:
            raise CommandError("GigaChat provider is not configured")

        models = list(AIModel.objects.filter(provider=provider, enabled=True).order_by("slug"))
        report = {
            "provider": "gigachat",
            "enabled": provider.enabled,
            "emergency_disabled": provider.emergency_disabled,
            "health_state": provider.health_state,
            "credential_source": provider.credential_source(),
            "credential_configured": provider.credential_configured(),
            "api_base_url_saved": provider.api_base_url or "",
            "api_base_url_effective": normalize_base_url(provider.api_base_url),
            "scope": str((provider.auth_config or {}).get("scope") or "GIGACHAT_API_PERS"),
            "ca_bundle": certifi.where(),
            "models": [
                {
                    "slug": model.slug,
                    "saved_upstream_model": model.upstream_model,
                    "effective_upstream_model": normalize_model_id(model.upstream_model),
                    "current_version": model.current_version.version if model.current_version_id else None,
                }
                for model in models
            ],
            "repairs": [],
            "health": None,
            "stream": None,
        }

        if repair:
            normalized_base = normalize_base_url(provider.api_base_url)
            if provider.api_base_url != normalized_base:
                provider.api_base_url = normalized_base
                provider.save(update_fields=["api_base_url"])
                report["repairs"].append(f"api_base_url -> {normalized_base}")
            for model in models:
                normalized_model = normalize_model_id(model.upstream_model)
                if model.upstream_model != normalized_model:
                    old = model.upstream_model
                    model.upstream_model = normalized_model
                    model.save(update_fields=["upstream_model"])
                    report["repairs"].append(f"{model.slug}: {old or '<empty>'} -> {normalized_model}")

        key = provider.get_api_key()
        if not key:
            report["health"] = {"ok": False, "error_code": "credential_missing"}
            self._finish(report, options["as_json"], success=False)
            return

        models = list(AIModel.objects.filter(provider=provider, enabled=True).order_by("slug"))
        if not models:
            report["health"] = {"ok": False, "error_code": "no_enabled_models"}
            self._finish(report, options["as_json"], success=False)
            return

        adapter = GigaChatAPIAdapter(
            authorization_key=key,
            base_url=provider.api_base_url or DEFAULT_API_BASE_URL,
            scope=str((provider.auth_config or {}).get("scope") or "GIGACHAT_API_PERS"),
        )

        health = adapter.health_check()
        report["health"] = {
            "ok": health.healthy,
            "latency_ms": health.latency_ms,
            "error_code": health.error_code,
        }
        if not health.healthy:
            self._mark_key(provider, ok=False, error_code=health.error_code, latency_ms=health.latency_ms)
            self._finish(report, options["as_json"], success=False)
            return

        model = models[0]
        output = []
        completed = None
        try:
            for event in adapter.stream(
                model=model.upstream_model,
                messages=[{"role": "user", "content": "Ответь ровно одним словом: OK"}],
                max_output_tokens=16,
            ):
                if event.kind == "delta":
                    output.append(event.text_delta)
                elif event.kind == "completed":
                    completed = event
        except ProviderError as exc:
            report["stream"] = {
                "ok": False,
                "error_code": exc.code,
                "retryable": exc.retryable,
                "model": normalize_model_id(model.upstream_model),
            }
            self._mark_key(provider, ok=False, error_code=exc.code)
            self._finish(report, options["as_json"], success=False)
            return

        text = "".join(output).strip()
        if completed is None or not text:
            report["stream"] = {
                "ok": False,
                "error_code": "empty_or_incomplete_stream",
                "model": normalize_model_id(model.upstream_model),
            }
            self._mark_key(provider, ok=False, error_code="empty_or_incomplete_stream")
            self._finish(report, options["as_json"], success=False)
            return

        report["stream"] = {
            "ok": True,
            "model": normalize_model_id(model.upstream_model),
            "received_text": True,
            "input_tokens": completed.input_tokens,
            "output_tokens": completed.output_tokens,
            "provider_request_id_present": bool(completed.provider_request_id),
        }
        self._mark_key(provider, ok=True, latency_ms=health.latency_ms)
        if repair:
            provider.enabled = True
            provider.health_state = Provider.HealthState.HEALTHY
            provider.consecutive_failures = 0
            provider.circuit_opened_until = None
            provider.last_checked_at = timezone.now()
            provider.last_latency_ms = health.latency_ms
            provider.save(update_fields=[
                "enabled",
                "health_state",
                "consecutive_failures",
                "circuit_opened_until",
                "last_checked_at",
                "last_latency_ms",
            ])
            report["repairs"].append("provider health/circuit -> healthy")

        self._finish(report, options["as_json"], success=True)

    def _mark_key(self, provider, *, ok, error_code="", latency_ms=None):
        key = ProviderApiKey.objects.filter(provider=provider, enabled=True).order_by("priority", "created_at").first()
        if key is None:
            return
        key.health_state = ProviderApiKey.HealthState.HEALTHY if ok else ProviderApiKey.HealthState.DEGRADED
        key.last_error_code = "" if ok else str(error_code or "gigachat_probe_failed")[:80]
        key.last_checked_at = timezone.now()
        if latency_ms is not None:
            key.last_latency_ms = latency_ms
        fields = ["health_state", "last_error_code", "last_checked_at"]
        if latency_ms is not None:
            fields.append("last_latency_ms")
        key.save(update_fields=fields)

    def _finish(self, report, as_json, *, success):
        if as_json:
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        else:
            self.stdout.write(f"GigaChat endpoint: {report['api_base_url_effective']}")
            self.stdout.write(f"Scope: {report['scope']}")
            self.stdout.write(f"Credential: {'configured' if report['credential_configured'] else 'missing'} ({report['credential_source']})")
            for item in report["models"]:
                self.stdout.write(f"Model {item['slug']}: {item['effective_upstream_model']}")
            for item in report["repairs"]:
                self.stdout.write(self.style.WARNING(f"REPAIR: {item}"))
            health = report.get("health") or {}
            self.stdout.write(f"Health: {'PASS' if health.get('ok') else 'FAIL'} {health.get('error_code', '')}")
            stream = report.get("stream") or {}
            if stream:
                self.stdout.write(f"Streaming: {'PASS' if stream.get('ok') else 'FAIL'} {stream.get('error_code', '')}")
            self.stdout.write(self.style.SUCCESS("GIGACHAT DIAGNOSE: PASS") if success else self.style.ERROR("GIGACHAT DIAGNOSE: FAIL"))
        if not success:
            raise CommandError("GigaChat probe failed; see error_code above")
