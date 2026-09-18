from datetime import timedelta

from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import BackupRecord, OperationalDrillEvidence, ReleaseRecord
from .permissions import IsPlatformAdmin


class OperationalDrillStatusView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        try:
            hours = min(max(int(request.query_params.get("max_age_hours", 168)), 1), 720)
        except (TypeError, ValueError):
            hours = 168
        cutoff = timezone.now() - timedelta(hours=hours)
        latest = {}
        for kind in OperationalDrillEvidence.Kind.values:
            item = OperationalDrillEvidence.objects.filter(kind=kind).first()
            latest[kind] = (
                {
                    "passed_at": item.passed_at,
                    "evidence_reference": item.evidence_reference,
                    "checksum_sha256": item.checksum_sha256,
                    "fresh": item.passed_at >= cutoff,
                }
                if item
                else None
            )
        restore = BackupRecord.objects.filter(
            status=BackupRecord.Status.RESTORED
        ).order_by("-restored_at").first()
        rollback = ReleaseRecord.objects.filter(
            state=ReleaseRecord.State.ROLLED_BACK,
            health_snapshot__drill=True,
        ).order_by("-created_at").first()
        restore_fresh = bool(restore and restore.restored_at and restore.restored_at >= cutoff)
        rollback_fresh = bool(rollback and rollback.created_at >= cutoff)
        ready = restore_fresh and rollback_fresh and all(
            item is not None and item["fresh"] for item in latest.values()
        )
        return Response(
            {
                "max_age_hours": hours,
                "ready": ready,
                "restore": {
                    "fresh": restore_fresh,
                    "restored_at": restore.restored_at if restore else None,
                    "evidence_reference": restore.storage_reference if restore else "",
                },
                "rollback": {
                    "fresh": rollback_fresh,
                    "created_at": rollback.created_at if rollback else None,
                    "evidence_reference": (
                        rollback.health_snapshot.get("evidence", "") if rollback else ""
                    ),
                },
                "drills": latest,
            }
        )
