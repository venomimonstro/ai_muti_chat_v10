import json

from django.core.management.base import BaseCommand, CommandError

from apps.admin_ops.system_health import list_issues, system_analysis


class Command(BaseCommand):
    help = "Проверяет текущие системные ошибки и состояние сервисов перед запуском."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Блокировать запуск при любой открытой или расследуемой ошибке.",
        )

    def handle(self, *args, **options):
        analysis = system_analysis()
        open_issues = list_issues(status="open", limit=500)
        investigating = list_issues(status="investigating", limit=500)
        blockers = []
        if analysis["state"] == "critical":
            blockers.append("Система находится в критическом состоянии")
        if options["strict"] and open_issues:
            blockers.append(f"Незакрытых системных ошибок: {len(open_issues)}")
        if options["strict"] and investigating:
            blockers.append(f"Ошибок в расследовании: {len(investigating)}")
        payload = {
            "ok": not blockers,
            "state": analysis["state"],
            "risk_score": analysis["risk_score"],
            "open_issues": len(open_issues),
            "investigating_issues": len(investigating),
            "unhealthy_providers": analysis["providers"]["unhealthy_count"],
            "ai_error_rate_24h_percent": analysis["ai"]["error_rate_24h_percent"],
            "payment_failures_24h": analysis["payments"]["failed_or_canceled_24h"],
            "blockers": blockers,
        }
        if options["as_json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, default=str))
        else:
            self.stdout.write(
                "Состояние: {state}; риск: {risk}/100; открыто: {open}; в работе: {investigating}".format(
                    state=payload["state"],
                    risk=payload["risk_score"],
                    open=payload["open_issues"],
                    investigating=payload["investigating_issues"],
                )
            )
        if blockers:
            raise CommandError("; ".join(blockers))
        if not options["as_json"]:
            self.stdout.write(self.style.SUCCESS("SYSTEM HEALTH: PASS"))
