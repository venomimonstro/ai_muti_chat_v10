from django.core.management.base import BaseCommand

from apps.ai_registry.web_tools import (
    WebToolError,
    _search_searx,
    _search_yandex,
    search_web,
    web_search_status,
)


class Command(BaseCommand):
    help = "Проверяет SearXNG/Yandex web-search и fallback без вывода секретов."

    def add_arguments(self, parser):
        parser.add_argument(
            "--query",
            default="официальный курс доллара сегодня",
            help="Безопасный тестовый поисковый запрос",
        )

    def handle(self, *args, **options):
        query = str(options["query"] or "").strip()[:400]
        status = web_search_status()
        order = status["provider_order"]
        self.stdout.write(f"Provider order: {', '.join(order)}")

        searx = status["searx"]
        self.stdout.write(
            "SearXNG: "
            + (f"configured · {searx['endpoint']}" if searx["configured"] else "not configured")
        )

        yandex = status["yandex"]
        self.stdout.write(
            "Yandex: "
            + (
                "configured"
                if yandex["configured"]
                else "not fully configured"
            )
            + f" · credential={yandex['credential_source']}"
            + f" · folder={'yes' if yandex['folder_configured'] else 'no'}"
            + f" · endpoint={yandex['endpoint']}"
        )

        if searx["configured"]:
            try:
                rows = _search_searx(query, limit=3)
                self.stdout.write(self.style.SUCCESS(f"SearXNG: PASS · results={len(rows)}"))
            except WebToolError as exc:
                self.stdout.write(self.style.WARNING(f"SearXNG: FAIL · {exc}"))

        if yandex["configured"]:
            try:
                rows = _search_yandex(query, limit=3)
                self.stdout.write(self.style.SUCCESS(f"Yandex: PASS · results={len(rows)}"))
            except WebToolError as exc:
                self.stdout.write(self.style.WARNING(f"Yandex: FAIL · {exc}"))

        try:
            rows = search_web(query, limit=3)
        except WebToolError as exc:
            self.stdout.write(self.style.ERROR(f"WEB SEARCH DIAGNOSE: FAIL · {exc}"))
            raise SystemExit(1) from exc

        self.stdout.write(self.style.SUCCESS(f"Fallback manager: PASS · results={len(rows)}"))
        for index, row in enumerate(rows[:3], start=1):
            self.stdout.write(f"  {index}. {row.title[:100]} · {row.url[:160]}")
        self.stdout.write(self.style.SUCCESS("WEB SEARCH DIAGNOSE: PASS"))
