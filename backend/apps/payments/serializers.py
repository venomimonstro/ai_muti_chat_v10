from decimal import Decimal

from rest_framework import serializers

from .models import Payment, Refund, RefundRequest


class PaymentSerializer(serializers.ModelSerializer):
    refundable_rub = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = (
            "id",
            "amount_rub",
            "currency",
            "status",
            "confirmation_url",
            "receipt_status",
            "credited_at",
            "refundable_rub",
            "created_at",
        )
        read_only_fields = fields

    def get_refundable_rub(self, obj):
        if obj.status != Payment.Status.SUCCEEDED:
            return "0.00"
        refund_states = {Refund.Status.CREATED, Refund.Status.PENDING, Refund.Status.SUCCEEDED}
        request_states = {
            RefundRequest.Status.PENDING,
            RefundRequest.Status.APPROVED,
            RefundRequest.Status.PROCESSING,
        }
        # .all() uses the prefetch cache on payment-list responses and remains
        # correct for single-payment serializer calls after create/sync.
        refunded = sum(
            (item.amount_rub for item in obj.refunds.all() if item.status in refund_states),
            Decimal("0.00"),
        )
        held = sum(
            (item.amount_rub for item in obj.refund_requests.all() if item.status in request_states),
            Decimal("0.00"),
        )
        payment_remaining = max(Decimal("0.00"), obj.amount_rub - refunded - held)
        wallet = getattr(obj.user, "wallet", None)
        unused_paid = max(Decimal("0.00"), wallet.paid_rub if wallet is not None else Decimal("0.00"))
        return f"{min(payment_remaining, unused_paid):.2f}"


class CreatePaymentSerializer(serializers.Serializer):
    amount_rub = serializers.DecimalField(max_digits=14, decimal_places=2)


class RefundSerializer(serializers.ModelSerializer):
    class Meta:
        model = Refund
        fields = ("id", "amount_rub", "status", "created_at")
        read_only_fields = fields


class CreateRefundSerializer(serializers.Serializer):
    amount_rub = serializers.DecimalField(max_digits=14, decimal_places=2)


class RefundRequestSerializer(serializers.ModelSerializer):
    payment_id = serializers.UUIDField(source="payment.id", read_only=True)
    refund_id = serializers.UUIDField(source="refund.id", read_only=True, allow_null=True)

    class Meta:
        model = RefundRequest
        fields = (
            "id",
            "payment_id",
            "refund_id",
            "amount_rub",
            "reason",
            "status",
            "admin_comment",
            "held_at",
            "resolved_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class CreateRefundRequestSerializer(serializers.Serializer):
    payment_id = serializers.UUIDField()
    amount_rub = serializers.DecimalField(max_digits=14, decimal_places=2)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=4000)


class ReviewRefundRequestSerializer(serializers.Serializer):
    admin_comment = serializers.CharField(required=False, allow_blank=True, max_length=4000)
