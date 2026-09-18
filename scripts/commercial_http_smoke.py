#!/usr/bin/env python3
"""Production-like HTTP smoke for a dedicated staging/production test account.

Required env:
  E2E_BASE_URL=https://staging.example.com
  E2E_USERNAME=...
  E2E_PASSWORD=...

The account must already be email-verified and have a small positive balance.
This script performs one real AI request and therefore incurs provider cost.
"""

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
    with httpx.Client(base_url=BASE, timeout=45.0, follow_redirects=False) as client:
        health = client.get("/api/v1/health/")
        health.raise_for_status()
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
            raise RuntimeError("No available AI model")
        wallet = client.get("/api/v1/wallet/")
        wallet.raise_for_status()
        wallet_before = wallet.json()
        balance_before = money(wallet_before.get("available_rub"))
        if balance_before <= ZERO:
            raise RuntimeError("E2E account has no available balance")
        if money(wallet_before.get("reserved_rub")) != ZERO:
            raise RuntimeError("E2E account already has reserved balance; use a dedicated idle account")
        conversation = client.post(
            "/api/v1/conversations/",
            json={
                "title": f"Commercial E2E {uuid.uuid4().hex[:8]}",
                "routing_mode": "balanced",
                "selected_model": available[0]["slug"],
            },
            headers=headers,
        )
        conversation.raise_for_status()
        conversation_id = conversation.json()["id"]
        request_headers = {
            **headers,
            "Idempotency-Key": f"e2e:{uuid.uuid4()}",
            "Accept": "text/event-stream",
        }
        saw_delta = False
        saw_error = None
        completed_event = None
        with client.stream(
            "POST",
            f"/api/v1/conversations/{conversation_id}/messages/stream/",
            json={
                "content": "Ответь одним словом: готово",
                "client_message_id": str(uuid.uuid4()),
            },
            headers=request_headers,
            timeout=120.0,
        ) as response:
            response.raise_for_status()
            event = "message"
            for line in response.iter_lines():
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    raw = line.split(":", 1)[1].strip()
                    if raw == "[DONE]":
                        continue
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if event == "delta" and payload.get("text"):
                        saw_delta = True
                    if event == "completed":
                        completed_event = payload
                    if event == "error":
                        saw_error = payload
        if saw_error:
            raise RuntimeError(f"AI stream error: {saw_error}")
        if not saw_delta:
            raise RuntimeError("AI stream returned no delta")
        if not completed_event:
            raise RuntimeError("AI stream did not emit completed event")
        event_cost = money(completed_event.get("cost_rub"))
        if event_cost <= ZERO:
            raise RuntimeError("Completed AI request has non-positive billed cost")

        final = client.get(f"/api/v1/conversations/{conversation_id}/")
        final.raise_for_status()
        messages = final.json().get("messages", [])
        assistant = next(
            (
                item
                for item in reversed(messages)
                if item.get("role") == "assistant" and item.get("content")
            ),
            None,
        )
        if assistant is None:
            raise RuntimeError("Completed conversation has no assistant response")
        generation = assistant.get("generation") or {}
        persisted_cost = money(generation.get("cost_rub"))
        if generation.get("state") != "completed":
            raise RuntimeError(f"Persisted generation is not completed: {generation.get('state')}")
        if persisted_cost <= ZERO:
            raise RuntimeError("Persisted generation has non-positive billed cost")
        if abs(persisted_cost - event_cost) > CENT:
            raise RuntimeError(
                f"SSE/persisted cost mismatch: event={event_cost}, persisted={persisted_cost}"
            )
        if not generation.get("provider") or not generation.get("model"):
            raise RuntimeError("Persisted generation is missing provider/model attribution")

        after_wallet = client.get("/api/v1/wallet/")
        after_wallet.raise_for_status()
        wallet_after = after_wallet.json()
        balance_after = money(wallet_after.get("available_rub"))
        reserved_after = money(wallet_after.get("reserved_rub"))
        if reserved_after != ZERO:
            raise RuntimeError(f"Reservation leaked after completed request: {reserved_after}")
        if balance_after >= balance_before:
            raise RuntimeError(
                f"Wallet was not debited: before={balance_before}, after={balance_after}"
            )
        balance_delta = balance_before - balance_after
        if abs(balance_delta - persisted_cost) > CENT:
            raise RuntimeError(
                f"Wallet debit/cost mismatch: debit={balance_delta}, cost={persisted_cost}"
            )

        print(
            json.dumps(
                {
                    "passed": True,
                    "conversation_id": conversation_id,
                    "model": generation.get("model"),
                    "provider": generation.get("provider"),
                    "cost_rub": str(persisted_cost),
                    "balance_before": str(balance_before),
                    "balance_after": str(balance_after),
                    "reserved_after": str(reserved_after),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        raise
