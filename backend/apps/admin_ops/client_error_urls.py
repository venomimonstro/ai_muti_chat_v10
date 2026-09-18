from django.urls import path

from .client_error_views import ClientErrorReportView

urlpatterns = [
    path("client-errors/", ClientErrorReportView.as_view(), name="client-error-report"),
]
