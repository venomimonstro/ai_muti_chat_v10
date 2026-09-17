import io
import json

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Run the final commercial launch gates as one auditable command"

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        gates = [
            ("django_deploy_check", "check", {"deploy": True}),
            ("commercial_config", "commercial_config_check", {"require_healthy": True}),
            ("payment_commercial", "payment_commercial_check", {"require_reconciliation": True}),
            ("financial_invariants", "verify_financial_invariants", {}),
            ("prelaunch_strict", "prelaunch_check", {"strict": True}),
        ]
        results = []
        for name, command, kwargs in gates:
            buffer = io.StringIO()
            try:
                call_command(command, stdout=buffer, stderr=buffer, **kwargs)
                results.append({"gate": name, "passed": True, "output": buffer.getvalue().strip()[-4000:]})
            except (CommandError, SystemExit, Exception) as exc:
                results.append(
                    {
                        "gate": name,
                        "passed": False,
                        "error": str(exc),
                        "output": buffer.getvalue().strip()[-4000:],
                    }
                )
        passed = all(item["passed"] for item in results)
        if options["as_json"]:
            self.stdout.write(json.dumps({"passed": passed, "gates": results}, ensure_ascii=False))
        else:
            for item in results:
                self.stdout.write(f"[{'PASS' if item['passed'] else 'BLOCK'}] {item['gate']}")
                if item.get("error"):
                    self.stdout.write(f"  {item['error']}")
        if not passed:
            raise CommandError("Commercial launch is BLOCKED; resolve every failed gate")
        self.stdout.write(self.style.SUCCESS("COMMERCIAL LAUNCH AUDIT: PASS"))
