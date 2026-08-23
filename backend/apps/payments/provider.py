import httpx
from django.conf import settings


class PaymentProviderError(Exception):
    pass


class YooKassaClient:
    def __init__(self, *, shop_id, secret_key, base_url=None):
        if not shop_id or not secret_key:
            raise PaymentProviderError("YooKassa credentials are not configured")
        self.auth = (shop_id, secret_key)
        self.base_url = (base_url or "https://api.yookassa.ru/v3").rstrip("/")

    @classmethod
    def from_settings(cls):
        return cls(
            shop_id=settings.YOOKASSA_SHOP_ID,
            secret_key=settings.YOOKASSA_SECRET_KEY,
            base_url=settings.YOOKASSA_API_BASE_URL,
        )

    def _request(self, method, path, *, key=None, payload=None):
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Idempotence-Key"] = key
        try:
            response = httpx.request(
                method,
                f"{self.base_url}{path}",
                auth=self.auth,
                headers=headers,
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PaymentProviderError("YooKassa request failed") from exc

    def create_payment(self, payload, idempotency_key):
        return self._request("POST", "/payments", key=idempotency_key, payload=payload)

    def get_payment(self, payment_id):
        return self._request("GET", f"/payments/{payment_id}")

    def create_refund(self, payload, idempotency_key):
        return self._request("POST", "/refunds", key=idempotency_key, payload=payload)

    def get_refund(self, refund_id):
        return self._request("GET", f"/refunds/{refund_id}")
