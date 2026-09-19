from django.conf import settings
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.admin_ops.permissions import IsPlatformAdmin

from .external_refunds import register_unknown_succeeded_refund
from .models import Payment, RefundRequest
from .provider import PaymentProviderError, YooKassaClient
from .refund_request_idempotency import create_refund_request_idempotent
from .serializers import (
    CreatePaymentSerializer,
    CreateRefundRequestSerializer,
    CreateRefundSerializer,
    PaymentSerializer,
    RefundRequestSerializer,
    RefundSerializer,
    ReviewRefundRequestSerializer,
)
from .services import (
    apply_payment_status,
    approve_refund_request,
    create_refund,
    create_topup,
    process_webhook,
    reject_refund_request,
)


def payment_readiness_payload():
    blockers = []
    if not settings.PAYMENTS_ENABLED:
        blockers.append("Пополнение отключено в настройках сервера")
    if not settings.YOOKASSA_SHOP_ID:
        blockers.append("Не указан YOOKASSA_SHOP_ID")
    if not settings.YOOKASSA_SECRET_KEY:
        blockers.append("Не указан YOOKASSA_SECRET_KEY")
    if settings.PAYMENTS_LIVE_ENABLED and settings.PAYMENTS_FISCALIZATION_MODE == "disabled":
        blockers.append("Для боевых платежей не настроена фискализация")
    return {
        "ready": not blockers,
        "enabled": settings.PAYMENTS_ENABLED,
        "live_enabled": settings.PAYMENTS_LIVE_ENABLED,
        "provider": "yookassa",
        "credentials_configured": bool(settings.YOOKASSA_SHOP_ID and settings.YOOKASSA_SECRET_KEY),
        "fiscalization_mode": settings.PAYMENTS_FISCALIZATION_MODE,
        "min_rub": str(settings.PAYMENT_MIN_RUB),
        "max_rub": str(settings.PAYMENT_MAX_RUB),
        "blockers": blockers,
    }


class PaymentViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PaymentSerializer

    def get_queryset(self):
        return Payment.objects.filter(user=self.request.user).order_by("-created_at")

    @action(detail=False, methods=["get"], url_path="readiness")
    def readiness(self, request):
        return Response(payment_readiness_payload())

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
        except PaymentProviderError as exc:
            return Response(
                {
                    "detail": (
                        "Не удалось подтвердить создание платежа в YooKassa. "
                        "Повторите запрос с тем же Idempotency-Key: новый платёж создан не будет."
                    ),
                    "code": "payment_status_unknown",
                    "provider_error": str(exc),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="sync")
    def sync(self, request, pk=None):
        payment = get_object_or_404(Payment, pk=pk, user=request.user)
        if not payment.provider_payment_id:
            return Response(
                {"detail": "Платёж ещё не связан с YooKassa", "code": "provider_payment_missing"},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            client = YooKassaClient.from_settings()
            remote = client.get_payment(payment.provider_payment_id)
            apply_payment_status(payment.id, remote)
        except ValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except PaymentProviderError as exc:
            return Response(
                {"detail": "Не удалось получить актуальный статус YooKassa", "code": "provider_sync_failed", "provider_error": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        payment.refresh_from_db()
        return Response(PaymentSerializer(payment).data)

    @action(detail=True, methods=["post"], permission_classes=[IsPlatformAdmin])
    def refunds(self, request, pk=None):
        payment = get_object_or_404(Payment, pk=pk)
        serializer = CreateRefundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = request.headers.get("Idempotency-Key", "")
        try:
            refund = create_refund(
                payment=payment,
                amount=serializer.validated_data["amount_rub"],
                idempotency_key=key,
            )
        except ValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except PaymentProviderError:
            return Response(
                {
                    "detail": (
                        "Статус возврата у провайдера пока неизвестен. Сумма остаётся удержанной; "
                        "повторите операцию с тем же Idempotency-Key или запустите сверку."
                    ),
                    "code": "refund_status_unknown",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(RefundSerializer(refund).data, status=status.HTTP_201_CREATED)


class RefundRequestViewSet(viewsets.ModelViewSet):
    http_method_names = ["get", "post", "head", "options"]

    def get_serializer_class(self):
        return CreateRefundRequestSerializer if self.action == "create" else RefundRequestSerializer

    def get_queryset(self):
        if IsPlatformAdmin().has_permission(self.request, self):
            return RefundRequest.objects.select_related("payment", "refund", "user").all()
        return RefundRequest.objects.select_related("payment", "refund").filter(user=self.request.user)

    def create(self, request):
        serializer = CreateRefundRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment = get_object_or_404(
            Payment,
            pk=serializer.validated_data["payment_id"],
            user=request.user,
        )
        key = request.headers.get("Idempotency-Key", "")
        try:
            refund_request = create_refund_request_idempotent(
                user=request.user,
                payment=payment,
                amount=serializer.validated_data["amount_rub"],
                reason=serializer.validated_data.get("reason", ""),
                idempotency_key=key,
            )
        except ValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            RefundRequestSerializer(refund_request).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], permission_classes=[IsPlatformAdmin])
    def approve(self, request, pk=None):
        item = get_object_or_404(RefundRequest, pk=pk)
        serializer = ReviewRefundRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            item = approve_refund_request(
                refund_request=item,
                admin_comment=serializer.validated_data.get("admin_comment", ""),
            )
        except ValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except PaymentProviderError:
            return Response(
                {
                    "detail": (
                        "Ответ YooKassa не подтверждён. Удержанная сумма НЕ возвращена на баланс, "
                        "чтобы исключить двойной возврат. Заявка оставлена в обработке и безопасно "
                        "повторяется/сверяется по тому же ключу."
                    ),
                    "code": "refund_status_unknown",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(RefundRequestSerializer(item).data)

    @action(detail=True, methods=["post"], permission_classes=[IsPlatformAdmin])
    def reject(self, request, pk=None):
        item = get_object_or_404(RefundRequest, pk=pk)
        serializer = ReviewRefundRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            item = reject_refund_request(
                refund_request=item,
                admin_comment=serializer.validated_data.get("admin_comment", ""),
            )
        except ValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(RefundRequestSerializer(item).data)


class YooKassaWebhookView(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        client = YooKassaClient.from_settings()
        try:
            register_unknown_succeeded_refund(request.data, client=client)
            process_webhook(request.data, client=client)
        except ValidationError:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        except PaymentProviderError:
            return Response(status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(status=status.HTTP_200_OK)
