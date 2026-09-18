from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from .compliance_manifest import bootstrap_compliance_manifest, required_compliance_keys
from .models import ComplianceSignoff
from .permissions import IsPlatformAdmin
from .services import audit


class ComplianceConsoleView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        bootstrap_compliance_manifest()
        required = required_compliance_keys()
        items = ComplianceSignoff.objects.filter(key__in=required).order_by("key")
        return Response([self._serialize(item) for item in items])

    def post(self, request):
        bootstrap_compliance_manifest()
        key = str(request.data.get("key", ""))
        if key not in required_compliance_keys():
            return Response({"detail": "Unknown or inactive compliance key"}, status=400)
        item = ComplianceSignoff.objects.filter(key=key).first()
        if item is None:
            return Response({"detail": "Compliance item not bootstrapped"}, status=409)
        signoff_status = request.data.get("status", ComplianceSignoff.Status.PENDING)
        if signoff_status not in ComplianceSignoff.Status.values:
            return Response({"detail": "Invalid sign-off status"}, status=400)
        evidence = str(request.data.get("evidence_reference", "")).strip()
        if signoff_status == ComplianceSignoff.Status.APPROVED and not evidence:
            return Response({"detail": "Approved sign-off requires evidence"}, status=400)
        item.status = signoff_status
        item.evidence_reference = evidence
        item.notes = str(request.data.get("notes", ""))
        item.reviewed_by = request.user if signoff_status != ComplianceSignoff.Status.PENDING else None
        item.reviewed_at = timezone.now() if signoff_status != ComplianceSignoff.Status.PENDING else None
        item.save()
        audit(request, "compliance_signoff.updated", "compliance_signoff", item.key, {"status": item.status, "evidence_reference": evidence})
        return Response(self._serialize(item))

    def _serialize(self, item):
        return {
            "key": item.key,
            "title": item.title,
            "status": item.status,
            "evidence_reference": item.evidence_reference,
            "notes": item.notes,
            "reviewed_by": item.reviewed_by_id,
            "reviewed_at": item.reviewed_at,
        }
