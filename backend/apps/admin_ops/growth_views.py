from datetime import timedelta

from django.db.models import Exists, OuterRef
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.chat.models import Generation
from apps.payments.models import Payment

from .permissions import IsPlatformAdmin


class GrowthFunnelView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        since = timezone.now() - timedelta(days=30)
        completed = Generation.objects.filter(owner_id=OuterRef("pk"), state=Generation.State.COMPLETED)
        paid = Payment.objects.filter(user_id=OuterRef("pk"), status=Payment.Status.SUCCEEDED)
        users = User.objects.filter(date_joined__gte=since).annotate(
            has_generation=Exists(completed), has_payment=Exists(paid)
        )
        registered = users.count()
        verified = users.filter(email_verified_at__isnull=False).count()
        activated = users.filter(has_generation=True).count()
        paying = users.filter(has_payment=True).count()

        def rate(value):
            return round(value / registered * 100, 2) if registered else 0

        return Response(
            {
                "period_days": 30,
                "registered": registered,
                "email_verified": verified,
                "activated": activated,
                "paying": paying,
                "rates_percent": {
                    "registration_to_verified": rate(verified),
                    "registration_to_first_answer": rate(activated),
                    "registration_to_payment": rate(paying),
                },
            }
        )
