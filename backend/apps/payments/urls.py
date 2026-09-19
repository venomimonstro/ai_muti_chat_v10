from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import PaymentViewSet, RefundRequestViewSet, YooKassaWebhookView

router = DefaultRouter()
router.register("payments", PaymentViewSet, basename="payment")
router.register("refund-requests", RefundRequestViewSet, basename="refund-request")
urlpatterns = router.urls + [
    path("payments/webhooks/yookassa/", YooKassaWebhookView.as_view(), name="yookassa-webhook")
]
