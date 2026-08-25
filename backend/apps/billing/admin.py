from django.contrib import admin

from .models import (
    BalanceReservation,
    BillingReconciliationItem,
    BillingReconciliationRun,
    CostAnomaly,
    FxRateSnapshot,
    LedgerEntry,
    MarginPolicyVersion,
    MarkupRuleVersion,
    PriceVersion,
    RequestCost,
    Wallet,
)


@admin.register(
    LedgerEntry,
    PriceVersion,
    RequestCost,
    FxRateSnapshot,
    MarkupRuleVersion,
    MarginPolicyVersion,
    BillingReconciliationRun,
    BillingReconciliationItem,
)
class ImmutableFinanceAdmin(admin.ModelAdmin):
    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(Wallet)
admin.site.register(BalanceReservation)
admin.site.register(CostAnomaly)
