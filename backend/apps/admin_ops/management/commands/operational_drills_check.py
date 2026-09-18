from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.admin_ops.models import BackupRecord, OperationalDrillEvidence, ReleaseRecord


class Command(BaseCommand):
    help = "Require recent restore, rollback and commercial operational drill evidence"

    def add_arguments(self, parser):
        parser.add_argument("--max-age-hours", type=int, default=168)

    def handle(self,*args,**options):
        cutoff=timezone.now()-timedelta(hours=max(1,options["max_age_hours"]))
        blockers=[]
        restore=BackupRecord.objects.filter(status=BackupRecord.Status.RESTORED,restored_at__gte=cutoff).exists()
        rollback=ReleaseRecord.objects.filter(state=ReleaseRecord.State.ROLLED_BACK,created_at__gte=cutoff,health_snapshot__drill=True).exists()
        if not restore: blockers.append("recent isolated restore drill missing")
        if not rollback: blockers.append("recent application rollback drill missing")
        for kind in OperationalDrillEvidence.Kind.values:
            if not OperationalDrillEvidence.objects.filter(kind=kind,passed_at__gte=cutoff).exists(): blockers.append(f"recent drill missing: {kind}")
        if blockers: raise CommandError("Operational drills BLOCKED: "+"; ".join(blockers))
        self.stdout.write(self.style.SUCCESS("OPERATIONAL DRILLS: PASS"))
