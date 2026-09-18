import json
import os
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.ai_registry.models import AIModel
from apps.billing.models import PriceVersion
from apps.image_studio.models import ImageModel


class Command(BaseCommand):
    help = "Проверяет, что функции, обещанные на публичном сайте, реально готовы к работе."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        blockers = []
        now = timezone.now()

        if settings.COMPARE_ENABLED:
            compare_models = []
            for model in AIModel.objects.filter(
                enabled=True,
                provider__enabled=True,
                provider__emergency_disabled=False,
                current_version__isnull=False,
            ).select_related("provider"):
                if "text" not in set(model.capabilities or []):
                    continue
                priced = PriceVersion.objects.filter(
                    model_slug=model.slug,
                    active=True,
                    effective_from__lte=now,
                    input_rub_per_million__gt=0,
                    output_rub_per_million__gt=0,
                ).exists()
                if priced:
                    compare_models.append(model.slug)
            if len(compare_models) < 2:
                blockers.append("Compare включён, но готовы менее двух текстовых моделей с ценами")
        else:
            compare_models = []

        image_models = []
        if settings.IMAGES_ENABLED:
            for model in ImageModel.objects.filter(
                enabled=True,
                provider__enabled=True,
                provider__emergency_disabled=False,
                provider_price_per_image__gt=0,
            ).select_related("provider"):
                credential_env = model.provider.credential_env
                if credential_env and not os.getenv(credential_env, "").strip():
                    continue
                image_models.append(model.slug)
            if not image_models:
                blockers.append(
                    "Генерация изображений включена, но нет готовой активной image-модели с ключом и себестоимостью"
                )

        web_url = os.getenv("WEB_SEARCH_BASE_URL", "").strip()
        parsed = urlparse(web_url) if web_url else None
        web_ready = bool(parsed and parsed.scheme in {"http", "https"} and parsed.hostname)
        if not web_ready:
            blockers.append("Не настроен WEB_SEARCH_BASE_URL, хотя web-поиск заявлен в продукте")

        payload = {
            "ok": not blockers,
            "compare_models": compare_models,
            "image_models": image_models,
            "web_search_configured": web_ready,
            "blockers": blockers,
        }
        if options["as_json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False))
        else:
            self.stdout.write(
                f"Compare моделей: {len(compare_models)}; image-моделей: {len(image_models)}; "
                f"web-search: {'готов' if web_ready else 'не настроен'}"
            )
        if blockers:
            raise CommandError("; ".join(blockers))
        if not options["as_json"]:
            self.stdout.write(self.style.SUCCESS("OPTIONAL FEATURES: PASS"))
