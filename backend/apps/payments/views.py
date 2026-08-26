from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.admin_ops.permissions import IsPlatformAdmin

from .models import Payment
from .provider import PaymentProviderError, YooKassaClient
from .serializers import (
    CreatePaymentSerializer,
    CreateRefundSerializer,
    PaymentSerializer,
    RefundSerializer,
)
from .services import create_refund, create_topup, process_webhook


class PaymentViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PaymentSerializer

    def get_queryset(self):
        return Payment.objects.filter(user=self.request.user).order_by("-created_at")

    def create(self, request):
        serializer = CreatePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = request.headers.get("Idempotency-Key", "")
        try:
            payment = create_topup(
                user=request.user,
                amount=serializer.validated_data["amount_rub"],
                idempotency_key=key,
            )
        except ValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except PaymentProviderError:
            return Response(
                {"detail": "Статус платежа уточняется. Повторите запрос с тем же ключом."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], permission_classes=[IsPlatformAdmin])
    def refunds(self, request, pk=None):
        payment = get_object_or_404(Payment, pk=pk)
        serializer = CreateRefundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            refund = create_refund(
                payment=payment,
                amount=serializer.validated_data["amount_rub"],
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
        except ValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(RefundSerializer(refund).data, status=status.HTTP_201_CREATED)


class YooKassaWebhookView(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "webhook"

    def post(self, request):
        event = request.data.get("event")
        if event not in {"payment.succeeded", "payment.canceled", "refund.succeeded", "refund.canceled"}:
            return Response(status=status.HTTP_200_OK)
        try:
            process_webhook(request.data, client=YooKassaClient.from_settings())
        except ValidationError:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        except PaymentProviderError:
            return Response(status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(status=status.HTTP_200_OK)
