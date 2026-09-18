import json
import os
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError

from apps.ai_registry.models import Provider
from apps.image_studio.models import ImageModel
from apps.image_studio.services import image_price_matrix_complete


class Command(BaseCommand):
    help = "Создаёт/обновляет коммерческую конфигурацию дополнительных функций."

    def handle(self, *args, **options):
        model_id = os.getenv("OPENAI_IMAGE_MODEL", "").strip()
        raw_price = os.getenv("OPENAI_IMAGE_PRICE_RUB", "").strip()
        raw_matrix = os.getenv("OPENAI_IMAGE_PRICE_MATRIX_JSON", "").strip()
        if not model_id and not raw_price and not raw_matrix:
            self.stdout.write("Image Studio: конфигурация не задана, изменений нет")
            return
        if not model_id or not raw_price:
            raise CommandError("Для Image Studio нужны OPENAI_IMAGE_MODEL и OPENAI_IMAGE_PRICE_RUB")
        try:
            price = Decimal(raw_price)
        except InvalidOperation as exc:
            raise CommandError("OPENAI_IMAGE_PRICE_RUB должен быть числом") from exc
        if price <= 0:
            raise CommandError("OPENAI_IMAGE_PRICE_RUB должен быть больше нуля")

        matrix = {}
        if raw_matrix:
            try:
                parsed = json.loads(raw_matrix)
            except json.JSONDecodeError as exc:
                raise CommandError("OPENAI_IMAGE_PRICE_MATRIX_JSON должен быть JSON-объектом") from exc
            if not isinstance(parsed, dict):
                raise CommandError("OPENAI_IMAGE_PRICE_MATRIX_JSON должен быть JSON-объектом")
            for key, raw_value in parsed.items():
                try:
                    value = Decimal(str(raw_value))
                except (InvalidOperation, TypeError, ValueError) as exc:
                    raise CommandError(f"Некорректная цена image-варианта {key}") from exc
                if value <= 0:
                    raise CommandError(f"Цена image-варианта {key} должна быть больше нуля")
                matrix[str(key)] = str(value)

        provider = Provider.objects.filter(slug="openai").first()
        if provider is None:
            raise CommandError("Провайдер OpenAI не создан; сначала выполните bootstrap_catalog")
        sizes = [
            item.strip()
            for item in os.getenv("OPENAI_IMAGE_SIZES", "1024x1024").split(",")
            if item.strip()
        ]
        qualities = [
            item.strip()
            for item in os.getenv("OPENAI_IMAGE_QUALITIES", "standard").split(",")
            if item.strip()
        ]
        max_images = max(1, min(4, int(os.getenv("OPENAI_IMAGE_MAX_IMAGES", "1"))))
        configured = bool(os.getenv(provider.credential_env or "OPENAI_API_KEY", "").strip())
        enabled = bool(provider.enabled and not provider.emergency_disabled and configured)
        image_model, created = ImageModel.objects.update_or_create(
            slug="openai-image-default",
            defaults={
                "provider": provider,
                "display_name": "Генерация изображений OpenAI",
                "upstream_model": model_id,
                "adapter_type": ImageModel.AdapterType.OPENAI_IMAGES,
                "enabled": enabled,
                "supported_sizes": sizes or ["1024x1024"],
                "supported_qualities": qualities or ["standard"],
                "max_images": max_images,
                "provider_currency": "RUB",
                "provider_price_per_image": price,
                "provider_price_matrix": matrix,
                "markup_percent": Decimal(os.getenv("OPENAI_IMAGE_MARKUP_PERCENT", "100")),
            },
        )
        if not image_price_matrix_complete(image_model):
            if image_model.enabled:
                image_model.enabled = False
                image_model.save(update_fields=["enabled"])
            raise CommandError(
                "Image Studio: для нескольких размеров/качеств задайте OPENAI_IMAGE_PRICE_MATRIX_JSON "
                "со всеми комбинациями вида 1024x1024|standard"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Image Studio: {'создана' if created else 'обновлена'} модель {image_model.slug}; "
                f"{'включена' if image_model.enabled else 'выключена до включения/настройки OpenAI'}"
            )
        )
