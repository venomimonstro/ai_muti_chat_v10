from django.urls import path

from .views import FinanceSummaryView, WalletView

urlpatterns = [
    path("wallet/", WalletView.as_view()),
    path("finance/summary/", FinanceSummaryView.as_view()),
]
