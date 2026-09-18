#!/usr/bin/env python3
"""Production B2B smoke using a dedicated verified non-admin user account."""

import json
import os
import sys
import uuid
from decimal import Decimal

import httpx

BASE = os.environ.get("E2E_BASE_URL", "").rstrip("/")
USERNAME = os.environ.get("E2E_USERNAME", "")
PASSWORD = os.environ.get("E2E_PASSWORD", "")
ZERO = Decimal("0")
CENT = Decimal("0.01")
ORG_NAME = "Commercial E2E API"


def require(value, name):
    if not value:
        raise SystemExit(f"{name} is required")


def money(value):
    return Decimal(str(value or "0"))


def csrf_headers(client, referer_path="/app"):
    response = client.get("/api/v1/auth/csrf/")
    response.raise_for_status()
    return {
        "X-CSRFToken": response.json()["csrf_token"],
        "Referer": f"{BASE}{referer_path}",
    }


def main():
    require(BASE, "E2E_BASE_URL")
    require(USERNAME, "E2E_USERNAME")
    require(PASSWORD, "E2E_PASSWORD")
    with httpx.Client(base_url=BASE, timeout=60.0, follow_redirects=False) as client:
        headers = csrf_headers(client, "/login")
        login = client.post(
            "/api/v1/auth/login/",
            json={"username": USERNAME, "password": PASSWORD},
            headers=headers,
        )
        login.raise_for_status()
        headers = csrf_headers(client)
        me = client.get("/api/v1/auth/me/")
        me.raise_for_status()
        if not me.json().get("email_verified"):
            raise RuntimeError("E2E account email is not verified")

        models = client.get("/api/v1/models/")
        models.raise_for_status()
        available = [item for item in models.json() if item.get("available")]
        if not available:
            raise RuntimeError("No available model for B2B smoke")
        model_slug = available[0]["slug"]

        wallet = client.get("/api/v1/wallet/")
        wallet.raise_for_status()
        before = wallet.json()
        balance_before = money(before.get("available_rub"))
        if balance_before <= ZERO:
            raise RuntimeError("E2E account has no available balance for B2B smoke")
        if money(before.get("reserved_rub")) != ZERO:
            raise RuntimeError("E2E account has an existing balance reservation")

        organizations = client.get("/api/v1/organizations/")
        organizations.raise_for_status()
        organization = next((item for item in organizations.json() if item.get("name") == ORG_NAME), None)
        if organization is None:
            created = client.post(
                "/api/v1/organizations/",
                json={"name": ORG_NAME},
                headers=headers,
            )
            created.raise_for_status()
            organization = created.json()
        if organization.get("monthly_limit_rub") not in {None, ""}:
            raise RuntimeError(
                "Dedicated B2B E2E organization has a cumulative monthly limit; recreate it without an organization limit"
            )
        organization_id = organization["id"]

        key_response = client.post(
            f"/api/v1/organizations/{organization_id}/keys/",
            json={
                "name": f"Launch smoke {uuid.uuid4().hex[:8]}",
                "scopes": ["chat.completions", "models.read", "usage.read"],
                "allowed_models": [model_slug],
                "allowed_endpoints": ["chat.completions"],
                "monthly_limit_rub": "20.00",
                "rate_limit_per_minute": 10,
                "max_concurrency": 1,
            },
            headers=headers,
        )
        key_response.raise_for_status()
        key_data = key_response.json()
        key_id = key_data["id"]
        secret = key_data.get("secret")
        if not secret:
            raise RuntimeError("B2B API key secret was not returned at creation")

        try:
            api_headers = {"Authorization": f"Bearer {secret}"}
            public_models = client.get("/v1/models", headers=api_headers)
            public_models.raise_for_status()
            model_ids = [item.get("id") for item in public_models.json().get("data", [])]
            if model_slug not in model_ids:
                raise RuntimeError("Allowed model is missing from /v1/models")

            completion = client.post(
                "/v1/chat/completions",
                headers={
                    **api_headers,
                    "Idempotency-Key": f"b2b-e2e:{uuid.uuid4()}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_slug,
                    "messages": [{"role": "user", "content": "Ответь одним словом: готово"}],
                    "max_completion_tokens": 32,
                },
                timeout=120.0,
            )
            completion.raise_for_status()
            body = completion.json()
            choices = body.get("choices") or []
            if not choices or not ((choices[0].get("message") or {}).get("content")):
                raise RuntimeError("B2B completion returned no assistant content")
            usage = body.get("usage") or {}
            if int(usage.get("total_tokens") or 0) <= 0:
                raise RuntimeError("B2B completion returned no token usage")

            usage_response = client.get("/v1/usage", headers=api_headers)
            usage_response.raise_for_status()
            usage_summary = usage_response.json()
            spend = money(usage_summary.get("spend_rub"))
            if int(usage_summary.get("requests") or 0) < 1 or spend <= ZERO:
                raise RuntimeError("B2B usage endpoint did not record the paid request")

            after_response = client.get("/api/v1/wallet/")
            after_response.raise_for_status()
            after = after_response.json()
            balance_after = money(after.get("available_rub"))
            reserved_after = money(after.get("reserved_rub"))
            if reserved_after != ZERO:
                raise RuntimeError(f"B2B reservation leaked after completion: {reserved_after}")
            debit = balance_before - balance_after
            if debit <= ZERO:
                raise RuntimeError("B2B API request did not debit the billing wallet")
            if abs(debit - spend) > CENT:
                raise RuntimeError(f"B2B wallet/usage mismatch: debit={debit}, spend={spend}")

            print(
                json.dumps(
                    {
                        "passed": True,
                        "organization_id": organization_id,
                        "model": model_slug,
                        "response_id": body.get("id"),
                        "tokens": usage.get("total_tokens"),
                        "spend_rub": str(spend),
                        "balance_before": str(balance_before),
                        "balance_after": str(balance_after),
                    },
                    ensure_ascii=False,
                )
            )
        finally:
            revoke = client.post(
                f"/api/v1/organizations/{organization_id}/keys/{key_id}/revoke/",
                headers=headers,
            )
            if revoke.status_code >= 400:
                print(
                    json.dumps(
                        {"warning": "failed_to_revoke_b2b_e2e_key", "status": revoke.status_code},
                        ensure_ascii=False,
                    ),
                    file=sys.stderr,
                )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise
