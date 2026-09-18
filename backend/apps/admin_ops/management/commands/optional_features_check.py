import json
import os
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.ai_registry.models import AIModel
from apps.ai_registry.web_tools import WebToolError, search_web
from apps.billing.models import PriceVersion
from apps.github_integration.services import app_jwt, configured as github_configured
from apps.github_integration.services import integration_enabled as github_enabled
from apps.image_studio.models import ImageModel


class Command(BaseCommand):
    help = "Проверяет, что функции, обещанные на публичном сайте, реально готовы к работе."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument(
            "--skip-live-web-probe",
            action="store_true",
            help="Пропустить внешний web-search probe; допустимо только для изолированных тестов.",
        )

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
        web_configured = bool(parsed and parsed.scheme in {"http", "https"} and parsed.hostname)
        web_probe_ok = False
        web_probe_results = 0
        if not web_configured:
            blockers.append("Не настроен WEB_SEARCH_BASE_URL, хотя web-поиск заявлен в продукте")
        elif options["skip_live_web_probe"]:
            web_probe_ok = True
        else:
            try:
                probe_results = search_web("OpenAI", limit=1)
                web_probe_results = len(probe_results)
                web_probe_ok = bool(probe_results)
                if not web_probe_ok:
                    blockers.append("Web-search отвечает, но production probe не вернул ни одного результата")
            except WebToolError as exc:
                blockers.append(f"Web-search production probe не прошёл: {exc}")

        if not settings.B2B_API_ENABLED:
            blockers.append("B2B OpenAI-compatible API выключен, хотя заявлен в продукте")

        github_required = os.getenv("GITHUB_REQUIRED_FOR_LAUNCH", "true").lower() == "true"
        github_is_enabled = github_enabled()
        github_is_configured = github_configured()
        github_key_valid = False
        if github_required and not github_is_enabled:
            blockers.append("GitHub интеграция выключена, хотя заявлена в коммерческом продукте")
        if github_is_enabled:
            if not github_is_configured:
                blockers.append("GitHub интеграция включена, но GitHub App настроен не полностью")
            else:
                try:
                    github_key_valid = bool(app_jwt())
                except ImproperlyConfigured as exc:
                    blockers.append(f"GitHub App private key не прошёл проверку: {exc}")

        payload = {
            "ok": not blockers,
            "compare_models": compare_models,
            "image_models": image_models,
            "web_search_configured": web_configured,
            "web_search_probe_ok": web_probe_ok,
            "web_search_probe_results": web_probe_results,
            "b2b_api_enabled": settings.B2B_API_ENABLED,
            "github_required_for_launch": github_required,
            "github_enabled": github_is_enabled,
            "github_configured": github_is_configured,
            "github_private_key_valid": github_key_valid,
            "blockers": blockers,
        }
        if options["as_json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False))
        else:
            self.stdout.write(
                f"Compare моделей: {len(compare_models)}; image-моделей: {len(image_models)}; "
                f"web-search: {'готов' if web_probe_ok else 'не готов'}; "
                f"B2B API: {'включён' if settings.B2B_API_ENABLED else 'выключен'}; "
                f"GitHub: {'готов' if github_is_enabled and github_is_configured and github_key_valid else 'выключен' if not github_is_enabled else 'не готов'}"
            )
        if blockers:
            raise CommandError("; ".join(blockers))
        if not options["as_json"]:
            self.stdout.write(self.style.SUCCESS("OPTIONAL FEATURES: PASS"))
