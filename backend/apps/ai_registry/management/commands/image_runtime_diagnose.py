from django.conf import settings
from django.core.management.base import BaseCommand

from apps.image_studio.models import ImageModel
from apps.image_studio.services import image_price_matrix_complete


class Command(BaseCommand):
    help = "Проверяет production-конфигурацию image generation/edit без платного API-вызова и без вывода секретов."

    def handle(self, *args, **options):
        if not settings.IMAGES_ENABLED:
            self.stdout.write(self.style.ERROR("Images: disabled by settings.IMAGES_ENABLED"))
            raise SystemExit(1)

        models = list(
            ImageModel.objects.select_related("provider")
            .filter(
                enabled=True,
                provider__enabled=True,
                provider__emergency_disabled=False,
            )
            .order_by("display_name")
        )
        if not models:
            self.stdout.write(self.style.ERROR("IMAGE RUNTIME DIAGNOSE: FAIL · no enabled image models"))
            raise SystemExit(1)

        usable = []
        for model in models:
            provider = model.provider
            credential_ok = provider.credential_configured()
            pricing_ok = image_price_matrix_complete(model)
            openai_adapter = model.adapter_type == ImageModel.AdapterType.OPENAI_IMAGES
            self.stdout.write(
                f"Model {model.slug}: adapter={model.adapter_type} · upstream={model.upstream_model} · "
                f"provider={provider.slug} · provider_health={provider.health_state} · "
                f"credential={'yes' if credential_ok else 'no'}({provider.credential_source()}) · "
                f"pricing={'complete' if pricing_ok else 'incomplete'} · "
                f"sizes={','.join(model.supported_sizes or []) or '-'} · "
                f"qualities={','.join(model.supported_qualities or []) or '-'}"
            )
            if openai_adapter and credential_ok and pricing_ok and model.upstream_model.strip():
                usable.append(model)

        if not usable:
            self.stdout.write(
                self.style.ERROR(
                    "IMAGE RUNTIME DIAGNOSE: FAIL · no usable OpenAI Images model with credential and complete pricing"
                )
            )
            raise SystemExit(1)

        self.stdout.write(
            self.style.SUCCESS(
                "IMAGE RUNTIME DIAGNOSE: PASS · "
                + ", ".join(model.slug for model in usable)
            )
        )
        self.stdout.write(
            "Live provider generation/edit is intentionally not called by this command to avoid paid diagnostic requests."
        )
