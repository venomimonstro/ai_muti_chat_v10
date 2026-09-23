from django.core.management.base import BaseCommand, CommandError

from apps.ai_registry.adapters import adapter_for
from apps.ai_registry.models import AIModel, RoutingPolicyVersion
from apps.chat.context import SYSTEM_POLICY


TIERS = {
    "economy": "System Lite",
    "balanced": "System Pro",
    "maximum": "System Max",
}


class Command(BaseCommand):
    help = "Live regression for BBTEC customer identity on every configured System tier"

    def handle(self, *args, **options):
        policy = RoutingPolicyVersion.objects.filter(active=True).first()
        configured = dict(((policy.thresholds if policy else {}) or {}).get("tier_models") or {})
        failures = []
        self.stdout.write("=== SYSTEM IDENTITY AUDIT ===")

        for mode, public_name in TIERS.items():
            slug = str(configured.get(mode) or "").strip()
            if not slug:
                failures.append(f"{public_name}: tier not configured")
                self.stdout.write(self.style.ERROR(f"[FAIL] {public_name}: tier not configured"))
                continue
            model = AIModel.objects.select_related("provider").filter(slug=slug, enabled=True).first()
            if model is None:
                failures.append(f"{public_name}: model unavailable")
                self.stdout.write(self.style.ERROR(f"[FAIL] {public_name}: model {slug} unavailable"))
                continue

            adapter = adapter_for(model)
            checks = [
                ("Кто ты?", "ваш агент", "identity"),
                ("Кто тебя создал?", "bbtec", "creator"),
            ]
            for question, needle, label in checks:
                try:
                    result = adapter.generate(
                        model=model.upstream_model,
                        messages=[
                            {"role": "system", "content": SYSTEM_POLICY + f"\nТекущий пользовательский уровень: {public_name}."},
                            {"role": "user", "content": question},
                        ],
                        max_output_tokens=64,
                    )
                    text = (result.text or "").strip()
                    if needle not in text.casefold():
                        failures.append(f"{public_name}/{label}: {text!r}")
                        self.stdout.write(self.style.ERROR(f"[FAIL] {public_name}/{label}: {text!r}"))
                    elif "gigachat" in text.casefold():
                        failures.append(f"{public_name}/{label}: provider identity leaked")
                        self.stdout.write(self.style.ERROR(f"[FAIL] {public_name}/{label}: provider leaked in {text!r}"))
                    else:
                        self.stdout.write(self.style.SUCCESS(f"[OK] {public_name}/{label}: {text!r}"))
                except Exception as exc:
                    failures.append(f"{public_name}/{label}: {type(exc).__name__}")
                    self.stdout.write(self.style.ERROR(f"[FAIL] {public_name}/{label}: {type(exc).__name__}: {exc}"))

        if failures:
            raise CommandError(f"System identity audit failed: {len(failures)} problem(s)")
        self.stdout.write(self.style.SUCCESS("System identity audit passed for all configured tiers"))
