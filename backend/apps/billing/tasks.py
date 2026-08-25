from celery import shared_task
from django.conf import settings

from apps.payments.services import reconcile_open_payments

from .reconciliation import reconcile_billing


@shared_task
def daily_financial_reconciliation():
    billing_run = reconcile_billing()
    payment_run = reconcile_open_payments() if settings.PAYMENTS_ENABLED else None
    return {
        "billing_run": str(billing_run.id),
        "payment_run": str(payment_run.id) if payment_run else None,
    }
