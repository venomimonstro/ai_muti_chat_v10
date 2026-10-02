from django.core.management.base import BaseCommand, CommandError

from apps.ai_registry.models import AIModel
from apps.ai_registry.reliability import model_client_ready


class Command(BaseCommand):
    help = (
        "Fail when production has no enabled AI model that can actually accept "
        "customer traffic (provider/key/model/pricing/procurement readiness)."
    )

    def handle(self, *args, **options):
        models = list(
            AIModel.objects.filter(
                enabled=True,
                provider__enabled=True,
                provider__emergency_disabled=False,
            )
            .select_related("provider", "current_version")
            .order_by("provider__priority", "slug")
        )
        ready = [model for model in models if model_client_ready(model)]
        blocked = [model for model in models if model not in ready]

        self.stdout.write(
            "CUSTOMER_AI_READINESS "
            f"enabled={len(models)} ready={len(ready)} blocked={len(blocked)}"
        )
        if ready:
            self.stdout.write(
                self.style.SUCCESS(
                    "CUSTOMER_AI_READY: "
                    + ", ".join(model.slug for model in ready[:20])
                )
            )
            return

        if not models:
            raise CommandError(
                "В production нет ни одной включённой AI-модели. "
                "Клиентский чат не может обслуживать запросы."
            )

        details = ", ".join(
            f"{model.slug}[provider={model.provider.slug};health={model.provider.health_state}]"
            for model in blocked[:20]
        )
        raise CommandError(
            "Ни одна включённая AI-модель не готова к клиентскому трафику. "
            "Проверьте HEALTHY API-ключи, funding accounts, закупочный баланс, "
            f"pricing и quarantine. Заблокированы: {details}"
        )
