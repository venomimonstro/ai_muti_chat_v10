from django.urls import path

from .analytics_views import ProductEventIngestView

urlpatterns = [
    path("events/", ProductEventIngestView.as_view(), name="product-event-ingest"),
]
