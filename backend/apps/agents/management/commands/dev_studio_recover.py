from django.core.management.base import BaseCommand, CommandError

from apps.agents.dev_recovery import DEFAULT_STALE_SECONDS, recover_stale_dev_run, stale_dev_runs


class Command(BaseCommand):
    help = "Inspect or fail-closed recover stale Dev Studio runs after worker interruption"

    def add_arguments(self, parser):
        parser.add_argument("--repair", action="store_true", help="Apply fail-closed recovery to stale Dev runs")
        parser.add_argument(
            "--older-than-seconds",
            type=int,
            default=DEFAULT_STALE_SECONDS,
            help="Stale threshold; minimum 300 seconds",
        )

    def handle(self, *args, **options):
        threshold = max(300, int(options["older_than_seconds"]))
        rows = list(stale_dev_runs(older_than_seconds=threshold))
        self.stdout.write("=== DEV STUDIO RECOVERY ===")
        self.stdout.write(f"stale_runs={len(rows)} threshold_seconds={threshold}")
        if not rows:
            self.stdout.write(self.style.SUCCESS("DEV_STUDIO_RECOVERY_OK"))
            return

        for run in rows:
            self.stdout.write(
                f"[STALE] run={run.id} state={run.state} updated_at={run.updated_at.isoformat()} "
                f"phase={(run.input_payload or {}).get('phase', '')}"
            )

        if not options["repair"]:
            raise CommandError(
                f"Found {len(rows)} stale Dev run(s). Re-run with --repair after checking worker state."
            )

        recovered = 0
        for run in rows:
            result = recover_stale_dev_run(run.id, older_than_seconds=threshold)
            if result.get("recovered"):
                recovered += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"[RECOVERED] run={run.id} customer_reservations="
                        f"{len(result.get('customer_reservations_released') or [])} provider_reservations="
                        f"{len(result.get('provider_reservations_released') or [])}"
                    )
                )

        if recovered != len(rows):
            raise CommandError(f"Recovery race detected: recovered={recovered} stale={len(rows)}")
        self.stdout.write(self.style.SUCCESS(f"DEV_STUDIO_RECOVERY_OK recovered={recovered}"))
