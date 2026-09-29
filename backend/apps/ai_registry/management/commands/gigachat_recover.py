import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.ai_registry.adapters import ProviderError
from apps.ai_registry.gigachat_adapter import (
    DEFAULT_API_BASE_URL,
    GigaChatAPIAdapter,
    normalize_base_url,
    normalize_model_id,
)
from apps.ai_registry.models import AIModel, Provider, ProviderApiKey, ReliabilityIncident


class Command(BaseCommand):
    help = (
        "Probe every enabled GigaChat API key without exposing secrets and recover "
        "provider/key health only after a real OAuth + models + streaming success."
    )

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        provider = Provider.objects.filter(slug="gigachat").first()
        if provider is None:
            raise CommandError("GigaChat provider is not configured")

        models = list(
            AIModel.objects.filter(provider=provider, enabled=True)
            .order_by("slug")
        )
        if not models:
            raise CommandError("GigaChat has no enabled models")

        keys = list(
            ProviderApiKey.objects.filter(provider=provider, enabled=True)
            .exclude(health_state=ProviderApiKey.HealthState.DISABLED)
            .order_by("priority", "created_at")
        )
        if not keys:
            raise CommandError("GigaChat has no enabled API keys")

        base_url = normalize_base_url(provider.api_base_url or DEFAULT_API_BASE_URL)
        scope = str((provider.auth_config or {}).get("scope") or "GIGACHAT_API_PERS")
        model = models[0]
        report = {
            "provider": "gigachat",
            "base_url": base_url,
            "scope": scope,
            "model": normalize_model_id(model.upstream_model),
            "provider_before": {
                "enabled": provider.enabled,
                "emergency_disabled": provider.emergency_disabled,
                "health_state": provider.health_state,
                "consecutive_failures": provider.consecutive_failures,
                "circuit_opened_until": provider.circuit_opened_until,
            },
            "keys": [],
            "recovered": False,
        }

        healthy_count = 0
        best_latency = None
        for index, key in enumerate(keys, start=1):
            item = {
                "index": index,
                "label": key.label,
                "before": key.health_state,
                "health": None,
                "stream": None,
            }
            secret = key.get_secret()
            if not secret:
                item["health"] = {"ok": False, "error_code": "credential_decrypt_failed"}
                self._mark_key(key, ok=False, error_code="credential_decrypt_failed")
                report["keys"].append(item)
                continue

            try:
                adapter = GigaChatAPIAdapter(
                    authorization_key=secret,
                    base_url=base_url,
                    scope=scope,
                )
                health = adapter.health_check()
            except ProviderError as exc:
                item["health"] = {
                    "ok": False,
                    "error_code": exc.code,
                    "retryable": exc.retryable,
                }
                self._mark_key(key, ok=False, error_code=exc.code)
                report["keys"].append(item)
                continue
            except Exception as exc:
                item["health"] = {
                    "ok": False,
                    "error_code": type(exc).__name__,
                }
                self._mark_key(key, ok=False, error_code=type(exc).__name__)
                report["keys"].append(item)
                continue

            item["health"] = {
                "ok": health.healthy,
                "latency_ms": health.latency_ms,
                "error_code": health.error_code,
            }
            if not health.healthy:
                self._mark_key(
                    key,
                    ok=False,
                    error_code=health.error_code or "gigachat_health_failed",
                    latency_ms=health.latency_ms,
                )
                report["keys"].append(item)
                continue

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
                item["stream"] = {
                    "ok": False,
                    "error_code": exc.code,
                    "retryable": exc.retryable,
                }
                self._mark_key(key, ok=False, error_code=exc.code, latency_ms=health.latency_ms)
                report["keys"].append(item)
                continue
            except Exception as exc:
                item["stream"] = {
                    "ok": False,
                    "error_code": type(exc).__name__,
                }
                self._mark_key(key, ok=False, error_code=type(exc).__name__, latency_ms=health.latency_ms)
                report["keys"].append(item)
                continue

            text = "".join(output).strip()
            if completed is None or not text:
                item["stream"] = {"ok": False, "error_code": "empty_or_incomplete_stream"}
                self._mark_key(key, ok=False, error_code="empty_or_incomplete_stream", latency_ms=health.latency_ms)
                report["keys"].append(item)
                continue

            item["stream"] = {
                "ok": True,
                "received_text": True,
                "input_tokens": completed.input_tokens,
                "output_tokens": completed.output_tokens,
                "provider_request_id_present": bool(completed.provider_request_id),
            }
            self._mark_key(key, ok=True, latency_ms=health.latency_ms)
            healthy_count += 1
            best_latency = health.latency_ms if best_latency is None else min(best_latency, health.latency_ms)
            report["keys"].append(item)

        if healthy_count:
            provider.enabled = True
            provider.emergency_disabled = False
            provider.health_state = Provider.HealthState.HEALTHY
            provider.consecutive_failures = 0
            provider.circuit_opened_until = None
            provider.last_checked_at = timezone.now()
            provider.last_latency_ms = best_latency
            provider.save(
                update_fields=[
                    "enabled",
                    "emergency_disabled",
                    "health_state",
                    "consecutive_failures",
                    "circuit_opened_until",
                    "last_checked_at",
                    "last_latency_ms",
                ]
            )
            ReliabilityIncident.objects.filter(
                provider=provider,
                state=ReliabilityIncident.State.OPEN,
            ).update(
                state=ReliabilityIncident.State.RECOVERED,
                recovered_at=timezone.now(),
            )
            report["recovered"] = True
        else:
            provider.health_state = Provider.HealthState.DEGRADED
            provider.last_checked_at = timezone.now()
            provider.save(update_fields=["health_state", "last_checked_at"])

        report["healthy_keys"] = healthy_count
        report["provider_after"] = {
            "enabled": provider.enabled,
            "emergency_disabled": provider.emergency_disabled,
            "health_state": provider.health_state,
            "consecutive_failures": provider.consecutive_failures,
            "circuit_opened_until": provider.circuit_opened_until,
        }

        if options["as_json"]:
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        else:
            self.stdout.write(f"GigaChat endpoint: {base_url}")
            self.stdout.write(f"Scope: {scope}")
            self.stdout.write(f"Model: {report['model']}")
            for item in report["keys"]:
                health = item.get("health") or {}
                stream = item.get("stream") or {}
                self.stdout.write(
                    " | ".join(
                        [
                            f"key={item['index']}:{item['label']}",
                            f"before={item['before']}",
                            f"health={'PASS' if health.get('ok') else 'FAIL'}:{health.get('error_code') or '-'}",
                            f"stream={'PASS' if stream.get('ok') else 'FAIL'}:{stream.get('error_code') or '-'}",
                        ]
                    )
                )
            self.stdout.write(f"HEALTHY_KEYS={healthy_count}")
            self.stdout.write(
                self.style.SUCCESS("GIGACHAT RECOVER: PASS")
                if healthy_count
                else self.style.ERROR("GIGACHAT RECOVER: FAIL")
            )

        if not healthy_count:
            raise CommandError("No working GigaChat API key was found")

    @staticmethod
    def _mark_key(key, *, ok, error_code="", latency_ms=None):
        key.health_state = (
            ProviderApiKey.HealthState.HEALTHY
            if ok
            else ProviderApiKey.HealthState.DEGRADED
        )
        key.last_error_code = "" if ok else str(error_code or "gigachat_probe_failed")[:80]
        key.last_checked_at = timezone.now()
        if latency_ms is not None:
            key.last_latency_ms = max(0, int(latency_ms))
        fields = ["health_state", "last_error_code", "last_checked_at"]
        if latency_ms is not None:
            fields.append("last_latency_ms")
        key.save(update_fields=fields)
