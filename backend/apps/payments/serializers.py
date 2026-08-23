from rest_framework import serializers

from .models import Payment, Refund


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
