import os

from django.core.management.base import BaseCommand, CommandError

from apps.ai_registry.web_tools import WebToolError, search_web
from apps.chat.live_tools import LiveToolError, current_time, current_weather


class Command(BaseCommand):
    help = "Check current time, live weather and configured web search end to end"

    def add_arguments(self, parser):
        parser.add_argument("--live", action="store_true", help="Call external weather/search providers")
        parser.add_argument("--city", default="Москва", help="City used for weather/time checks")
        parser.add_argument("--query", default="официальный сайт Яндекс", help="Web search smoke query")
        parser.add_argument("--require-yandex", action="store_true", help="Fail if Yandex Search is not configured")

    def handle(self, *args, **options):
        failures = 0
        self.stdout.write("=== LIVE TOOLS AUDIT ===")

        try:
            clock = current_time(options["city"] if options["live"] else "")
            self.stdout.write(
                self.style.SUCCESS(
                    f"[OK] clock place={clock['place']} timezone={clock['timezone']} time={clock['time']}"
                )
            )
        except LiveToolError as exc:
            failures += 1
            self.stdout.write(self.style.ERROR(f"[FAIL] clock: {exc}"))

        if options["live"]:
            try:
                weather = current_weather(options["city"])
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[OK] weather place={weather['place']} temp={weather['temperature_c']}C "
                        f"condition={weather['condition']}"
                    )
                )
            except LiveToolError as exc:
                failures += 1
                self.stdout.write(self.style.ERROR(f"[FAIL] weather: {exc}"))
        else:
            self.stdout.write("[SKIP] weather: use --live")

        yandex_configured = bool(
            (os.getenv("YANDEX_SEARCH_API_KEY", "").strip() or os.getenv("SEARCH_API_KEY", "").strip())
            and (os.getenv("YANDEX_SEARCH_FOLDER_ID", "").strip() or os.getenv("FOLDER_ID", "").strip())
        )
        if not options["live"]:
            self.stdout.write("[SKIP] web search: use --live")
        elif not yandex_configured and not os.getenv("WEB_SEARCH_BASE_URL", "").strip():
            message = "Yandex Search credentials and WEB_SEARCH_BASE_URL are absent"
            if options["require_yandex"]:
                failures += 1
                self.stdout.write(self.style.ERROR(f"[FAIL] web search: {message}"))
            else:
                self.stdout.write(f"[SKIP] web search: {message}")
        else:
            try:
                results = search_web(options["query"], limit=3)
                provider = "yandex" if yandex_configured else "fallback"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[OK] web_search provider={provider} results={len(results)} "
                        f"first={results[0].url if results else '-'}"
                    )
                )
            except WebToolError as exc:
                failures += 1
                self.stdout.write(self.style.ERROR(f"[FAIL] web search: {exc}"))

        if failures:
            raise CommandError(f"Live tools audit failed: {failures} problem(s)")
        self.stdout.write(self.style.SUCCESS("LIVE TOOLS AUDIT PASSED"))
