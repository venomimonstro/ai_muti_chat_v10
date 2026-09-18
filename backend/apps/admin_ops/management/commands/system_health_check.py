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
            help="Блокировать запуск при критической открытой или расследуемой ошибке.",
        )

    def handle(self, *args, **options):
        analysis = system_analysis()
        open_issues = list_issues(status="open", limit=500)
        investigating = list_issues(status="investigating", limit=500)
        critical_open = [item for item in open_issues if item.get("severity") == "critical"]
        critical_investigating = [
            item for item in investigating if item.get("severity") == "critical"
        ]
        warning_open = [item for item in open_issues if item.get("severity") == "warning"]
        blockers = []
        if analysis["state"] == "critical":
            blockers.append("Система находится в критическом состоянии")
        if options["strict"] and critical_open:
            blockers.append(f"Незакрытых критических системных ошибок: {len(critical_open)}")
        if options["strict"] and critical_investigating:
            blockers.append(
                f"Критических ошибок в расследовании: {len(critical_investigating)}"
            )
        payload = {
            "ok": not blockers,
            "state": analysis["state"],
            "risk_score": analysis["risk_score"],
            "open_issues": len(open_issues),
            "investigating_issues": len(investigating),
            "critical_open_issues": len(critical_open),
            "critical_investigating_issues": len(critical_investigating),
            "warning_open_issues": len(warning_open),
            "unhealthy_providers": analysis["providers"]["unhealthy_count"],
            "ai_error_rate_24h_percent": analysis["ai"]["error_rate_24h_percent"],
            "payment_failures_24h": analysis["payments"]["failed_or_canceled_24h"],
            "blockers": blockers,
        }
        if options["as_json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, default=str))
        else:
            self.stdout.write(
                "Состояние: {state}; риск: {risk}/100; критических открытых: {critical}; предупреждений: {warnings}".format(
                    state=payload["state"],
                    risk=payload["risk_score"],
                    critical=payload["critical_open_issues"],
                    warnings=payload["warning_open_issues"],
                )
            )
        if blockers:
            raise CommandError("; ".join(blockers))
        if not options["as_json"]:
            self.stdout.write(self.style.SUCCESS("SYSTEM HEALTH: PASS"))
