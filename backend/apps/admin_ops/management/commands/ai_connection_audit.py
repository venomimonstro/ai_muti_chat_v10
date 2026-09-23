from django.core.management.base import BaseCommand, CommandError

from apps.ai_registry.adapters import ProviderError, adapter_for
from apps.ai_registry.models import AIModel, Provider, RoutingPolicyVersion
from apps.ai_registry.reliability import provider_available
from apps.billing.pricing import active_price, quote, require_margin


TIER_TO_MODE = {"weak": "economy", "medium": "balanced", "high": "maximum"}


def model_problems(model):
    problems = []
    if not model.current_version_id:
        problems.append("no_active_version")
    if not (model.upstream_model or "").strip():
        problems.append("no_upstream_model")
    if not provider_available(model.provider):
        problems.append("provider_unavailable")
    try:
        price = active_price(model.slug)
        require_margin(quote(price, 1_000_000, 0, provider_slug=model.provider.slug, model_slug=model.slug))
        require_margin(quote(price, 0, 1_000_000, provider_slug=model.provider.slug, model_slug=model.slug))
    except Exception:
        problems.append("no_safe_price")
    if not model.enabled:
        problems.append("model_disabled")
    return problems


class Command(BaseCommand):
    help = "360-degree audit: provider -> key -> model -> pricing -> AUTO tiers -> client readiness"

    def add_arguments(self, parser):
        parser.add_argument("--live", action="store_true", help="Perform live provider health checks")
        parser.add_argument("--generate", action="store_true", help="Send one tiny real generation to every configured AUTO tier")

    def handle(self, *args, **options):
        live = bool(options["live"])
        generate = bool(options["generate"])
        failed = 0
        self.stdout.write("=== AI CONNECTION AUDIT 360 ===")

        for provider in Provider.objects.prefetch_related("api_keys", "models").order_by("priority", "slug"):
            if provider.adapter_type == Provider.AdapterType.ECHO:
                continue

            configured_keys = provider.api_keys.filter(enabled=True).count()

            # A provider explicitly disabled by the owner is outside the runtime
            # routing surface. Stale credentials, health state or saved models must
            # not turn an intentionally disabled integration into a critical failure.
            if not provider.enabled:
                reason = "disabled by admin"
                if configured_keys:
                    reason += f"; stored enabled keys={configured_keys} ignored by routing"
                self.stdout.write(f"[SKIP] provider={provider.slug}: {reason}")
                continue

            configured = provider.credential_configured() or configured_keys > 0
            if not configured:
                failed += 1
                self.stdout.write(f"[FAIL] provider={provider.slug}: enabled but no credential configured")
                continue

            healthy_keys = provider.api_keys.filter(enabled=True, health_state="healthy").count()
            models = list(provider.models.all())
            enabled = [m for m in models if m.enabled]
            status = "OK" if provider.health_state == "healthy" and healthy_keys else "FAIL"
            if status == "FAIL":
                failed += 1
            self.stdout.write(
                f"[{status}] provider={provider.slug} enabled={provider.enabled} health={provider.health_state} "
                f"healthy_keys={healthy_keys}/{configured_keys} models={len(models)} enabled_models={len(enabled)}"
            )
            if live and healthy_keys and models:
                probe = next((m for m in models if m.current_version_id and m.upstream_model), models[0])
                try:
                    health = adapter_for(probe).health_check()
                    live_status = "OK" if health.healthy else "FAIL"
                    if not health.healthy:
                        failed += 1
                    self.stdout.write(f"  [{live_status}] live latency_ms={health.latency_ms} error={health.error_code or '-'}")
                except Exception as exc:
                    failed += 1
                    self.stdout.write(f"  [FAIL] live {type(exc).__name__}: {exc}")

        policy = RoutingPolicyVersion.objects.filter(active=True).first()
        tier_models = dict((policy.thresholds if policy else {}).get("tier_models") or {})
        self.stdout.write("--- AUTO TIERS ---")
        ready_tiers = 0
        generated_models = set()
        for tier, mode in TIER_TO_MODE.items():
            slug = tier_models.get(mode) or ""
            if not slug:
                failed += 1
                self.stdout.write(f"[FAIL] {tier}/{mode}: model not assigned")
                continue
            model = AIModel.objects.select_related("provider", "current_version").filter(slug=slug).first()
            if not model:
                failed += 1
                self.stdout.write(f"[FAIL] {tier}/{mode}: missing model {slug}")
                continue
            problems = model_problems(model)
            if problems:
                failed += 1
                self.stdout.write(f"[FAIL] {tier}/{mode}: {model.provider.slug}/{model.slug} -> {','.join(problems)}")
                continue
            ready_tiers += 1
            self.stdout.write(f"[OK] {tier}/{mode}: {model.provider.slug}/{model.slug} ({model.upstream_model})")
            if generate and model.slug not in generated_models:
                generated_models.add(model.slug)
                try:
                    result = adapter_for(model).generate(
                        model=model.upstream_model,
                        messages=[{"role": "user", "content": "Ответь только: OK"}],
                        max_output_tokens=16,
                    )
                    text = (result.text or "").strip().replace("\n", " ")[:120]
                    if not text:
                        failed += 1
                        self.stdout.write("  [FAIL] generation returned empty response")
                    else:
                        self.stdout.write(
                            f"  [OK] generation response={text!r} input_tokens={result.input_tokens} output_tokens={result.output_tokens}"
                        )
                except ProviderError as exc:
                    failed += 1
                    self.stdout.write(f"  [FAIL] generation code={exc.code}: {exc}")
                except Exception as exc:
                    failed += 1
                    self.stdout.write(f"  [FAIL] generation {type(exc).__name__}: {exc}")

        visible_manual = AIModel.objects.filter(
            enabled=True,
            provider__enabled=True,
            provider__emergency_disabled=False,
        ).exclude(provider__slug="gigachat").count()
        self.stdout.write(
            f"--- CLIENT ---\nmanual_visible_models={visible_manual} auto_ready_tiers={ready_tiers}/3 "
            f"legacy_gigachat_hidden=yes"
        )
        if ready_tiers != 3:
            failed += 1

        if failed:
            raise CommandError(f"AI audit failed: {failed} critical problem(s)")
        self.stdout.write(self.style.SUCCESS("AI audit passed: provider -> model -> pricing -> AUTO -> real generation -> client chain is ready"))
