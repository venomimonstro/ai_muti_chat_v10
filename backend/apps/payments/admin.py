from django.contrib import admin

from .models import (
    Payment,
    PaymentCostSnapshot,
    PaymentEvent,
    PaymentFeeVersion,
    ReconciliationRun,
    Refund,
)


@admin.register(PaymentEvent, PaymentCostSnapshot, ReconciliationRun)
class ReadOnlyPaymentAdmin(admin.ModelAdmin):
    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaymentFeeVersion)
class ImmutableFeeAdmin(ReadOnlyPaymentAdmin):
    pass


admin.site.register(Payment)
admin.site.register(Refund)
