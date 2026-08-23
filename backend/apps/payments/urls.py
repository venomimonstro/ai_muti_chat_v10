from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import PaymentViewSet, YooKassaWebhookView

router = DefaultRouter()
router.register("payments", PaymentViewSet, basename="payment")
urlpatterns = router.urls + [
    path("payments/webhooks/yookassa/", YooKassaWebhookView.as_view(), name="yookassa-webhook")
]
