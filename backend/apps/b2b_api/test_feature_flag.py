import pytest
from django.test import override_settings
from rest_framework.test import APIClient


@pytest.mark.django_db
@override_settings(B2B_API_ENABLED=False)
def test_public_api_feature_flag_fails_closed_before_authentication():
    response = APIClient().get("/v1/models")
    assert response.status_code == 503
    assert response.data["error"]["code"] == "api_disabled"
