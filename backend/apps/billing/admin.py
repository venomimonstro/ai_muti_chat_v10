from django.contrib import admin

from .models import BalanceReservation, LedgerEntry, PriceVersion, RequestCost, Wallet


@admin.register(LedgerEntry, PriceVersion, RequestCost)
class ImmutableFinanceAdmin(admin.ModelAdmin):
    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(Wallet)
admin.site.register(BalanceReservation)
