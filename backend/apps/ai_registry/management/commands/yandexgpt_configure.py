from django.core.management.base import BaseCommand, CommandError

from apps.ai_registry.models import AIModel, Provider


class Command(BaseCommand):
    help = "Configure YandexGPT folder/model metadata without exposing API keys in shell history."

    def add_arguments(self, parser):
        parser.add_argument("--folder-id", required=True)
        parser.add_argument("--upstream-model", default="yandexgpt/latest")

    def handle(self, *args, **options):
        folder_id = str(options["folder_id"] or "").strip()
        upstream = str(options["upstream_model"] or "").strip()
        if not folder_id:
            raise CommandError("--folder-id обязателен")
        if not upstream:
            raise CommandError("--upstream-model обязателен")

        provider = Provider.objects.filter(slug="yandexgpt").first()
        model = AIModel.objects.filter(slug="yandexgpt", provider=provider).first() if provider else None
        if provider is None or model is None:
            raise CommandError("YandexGPT registry seed не найден. Сначала выполните migrate.")

        provider.auth_config = {**(provider.auth_config or {}), "folder_id": folder_id}
        provider.api_base_url = provider.api_base_url or "https://ai.api.cloud.yandex.net/v1"
        provider.enabled = True
        if provider.health_state == Provider.HealthState.DISABLED:
            provider.health_state = Provider.HealthState.UNKNOWN
        provider.save(update_fields=["auth_config", "api_base_url", "enabled", "health_state"])

        model.upstream_model = upstream
        model.enabled = True
        model.save(update_fields=["upstream_model", "enabled"])

        self.stdout.write(self.style.SUCCESS("YandexGPT metadata configured."))
        self.stdout.write(
            "Теперь добавьте API-ключ в пул ключей провайдера YandexGPT, привяжите funding account, "
            "задайте закупочные цены и выполните provider health-check. Ключ в эту команду не передавайте."
        )
