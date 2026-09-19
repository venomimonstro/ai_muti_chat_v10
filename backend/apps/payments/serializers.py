from rest_framework import serializers

from .models import Payment, Refund, RefundRequest


class PaymentSerializer(serializers.ModelSerializer):
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
            "created_at",
        )
        read_only_fields = fields


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
