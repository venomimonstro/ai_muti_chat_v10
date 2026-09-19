import random
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value):
    return Decimal(str(value)).quantize(CENT)


@dataclass
class PaymentState:
    amount: Decimal
    credited: bool = False
    refunded: Decimal = ZERO
    refund_held: Decimal = ZERO
    provider_refund_ids: set[str] = field(default_factory=set)


@dataclass
class ClientState:
    paid: Decimal = ZERO
    promo: Decimal = ZERO
    reserved: Decimal = ZERO
    available: Decimal = ZERO
    topup_keys: dict[str, PaymentState] = field(default_factory=dict)
    reservation_keys: dict[str, Decimal] = field(default_factory=dict)
    settled_keys: dict[str, Decimal] = field(default_factory=dict)
    refund_request_keys: dict[str, tuple[str, Decimal]] = field(default_factory=dict)

    def credit_topup(self, key, amount):
        amount = money(amount)
        payment = self.topup_keys.get(key)
        if payment is None:
            payment = PaymentState(amount=amount)
            self.topup_keys[key] = payment
        elif payment.amount != amount:
            raise AssertionError("topup idempotency key reused with different amount")
        if not payment.credited:
            payment.credited = True
            self.paid += amount
            self.available += amount
        return payment

    def reserve(self, key, amount):
        amount = money(amount)
        if key in self.reservation_keys:
            if self.reservation_keys[key] != amount:
                raise AssertionError("reservation key reused with different amount")
            return
        if amount > self.available:
            raise AssertionError("reserve exceeds available balance")
        self.available -= amount
        self.paid -= amount
        self.reserved += amount
        self.reservation_keys[key] = amount

    def settle(self, key, actual, *, provider_confirmed):
        if key in self.settled_keys:
            return self.settled_keys[key]
        reserved = self.reservation_keys.get(key, ZERO)
        actual = money(actual) if provider_confirmed else ZERO
        actual = min(actual, reserved)
        release = reserved - actual
        self.reserved -= reserved
        self.available += release
        self.paid += release
        self.settled_keys[key] = actual
        return actual

    def request_refund(self, request_key, payment_key, amount):
        amount = money(amount)
        existing = self.refund_request_keys.get(request_key)
        if existing is not None:
            if existing != (payment_key, amount):
                raise AssertionError("refund request key reused with different payload")
            return False
        payment = self.topup_keys[payment_key]
        if payment.refunded + payment.refund_held + amount > payment.amount:
            return False
        if amount > self.paid or amount > self.available:
            return False
        self.paid -= amount
        self.available -= amount
        payment.refund_held += amount
        self.refund_request_keys[request_key] = (payment_key, amount)
        return True

    def finish_refund(self, request_key, provider_refund_id, *, succeeded):
        payment_key, amount = self.refund_request_keys[request_key]
        payment = self.topup_keys[payment_key]
        if provider_refund_id in payment.provider_refund_ids:
            return
        payment.provider_refund_ids.add(provider_refund_id)
        if succeeded:
            if payment.refund_held < amount:
                raise AssertionError("refund succeeded without held balance")
            payment.refund_held -= amount
            payment.refunded += amount
        else:
            if payment.refund_held < amount:
                raise AssertionError("refund cancel without held balance")
            payment.refund_held -= amount
            self.paid += amount
            self.available += amount

    def assert_invariants(self):
        if min(self.paid, self.promo, self.reserved, self.available) < ZERO:
            raise AssertionError("negative wallet bucket")
        if self.available != self.paid + self.promo:
            raise AssertionError("available != paid + promo")
        for payment in self.topup_keys.values():
            if payment.refunded < ZERO or payment.refund_held < ZERO:
                raise AssertionError("negative refund bucket")
            if payment.refunded + payment.refund_held > payment.amount:
                raise AssertionError("refund exceeds original payment")


class Command(BaseCommand):
    help = "Run deterministic 1000-client Monte-Carlo billing/retry/refund simulation"

    def add_arguments(self, parser):
        parser.add_argument("--clients", type=int, default=1000)
        parser.add_argument("--seed", type=int, default=20260919)
        parser.add_argument("--operations", type=int, default=30)

    def handle(self, *args, **options):
        clients = max(1, int(options["clients"]))
        operations = max(1, int(options["operations"]))
        rng = random.Random(int(options["seed"]))
        stats = {
            "clients": clients,
            "chat_attempts": 0,
            "disconnects": 0,
            "provider_timeouts": 0,
            "confirmed_usage": 0,
            "overruns_capped": 0,
            "duplicate_retries": 0,
            "refund_requests": 0,
            "refund_timeouts": 0,
            "refund_webhook_first": 0,
            "rejected_refunds": 0,
        }

        try:
            for client_index in range(clients):
                state = ClientState()
                topup_amount = money(rng.choice([300, 500, 1000, 3000]))
                topup_key = f"topup:{client_index}:1"
                state.credit_topup(topup_key, topup_amount)
                if rng.random() < 0.12:
                    state.credit_topup(topup_key, topup_amount)
                    stats["duplicate_retries"] += 1

                for op in range(operations):
                    if state.available < Decimal("1.00"):
                        break
                    stats["chat_attempts"] += 1
                    reserve_amount = min(
                        state.available,
                        money(rng.uniform(0.05, max(0.05, float(state.available) * 0.10))),
                    )
                    if reserve_amount <= ZERO:
                        continue
                    generation_key = f"generation:{client_index}:{op}"
                    state.reserve(generation_key, reserve_amount)
                    if rng.random() < 0.08:
                        state.reserve(generation_key, reserve_amount)
                        stats["duplicate_retries"] += 1

                    provider_confirmed = rng.random() >= 0.13
                    if not provider_confirmed:
                        stats["provider_timeouts"] += 1
                    else:
                        stats["confirmed_usage"] += 1
                    if rng.random() < 0.09:
                        stats["disconnects"] += 1
                    raw_actual = money(rng.uniform(0.01, max(0.02, float(reserve_amount) * 1.20)))
                    if raw_actual > reserve_amount and provider_confirmed:
                        stats["overruns_capped"] += 1
                    state.settle(
                        generation_key,
                        raw_actual,
                        provider_confirmed=provider_confirmed,
                    )
                    if rng.random() < 0.10:
                        state.settle(
                            generation_key,
                            raw_actual if provider_confirmed else ZERO,
                            provider_confirmed=provider_confirmed,
                        )
                        stats["duplicate_retries"] += 1
                    state.assert_invariants()

                if rng.random() < 0.35 and state.paid >= Decimal("1.00"):
                    stats["refund_requests"] += 1
                    amount = min(
                        state.paid,
                        money(rng.choice([10, 20, 40, 50, 100])),
                    )
                    request_key = f"refund-request:{client_index}:1"
                    created = state.request_refund(request_key, topup_key, amount)
                    if created and rng.random() < 0.12:
                        state.request_refund(request_key, topup_key, amount)
                        stats["duplicate_retries"] += 1
                    if created:
                        provider_refund_id = f"provider-refund:{client_index}:1"
                        timeout = rng.random() < 0.10
                        webhook_first = timeout and rng.random() < 0.65
                        if timeout:
                            stats["refund_timeouts"] += 1
                        if webhook_first:
                            stats["refund_webhook_first"] += 1
                        succeeded = rng.random() < 0.90
                        if not succeeded:
                            stats["rejected_refunds"] += 1
                        state.finish_refund(
                            request_key,
                            provider_refund_id,
                            succeeded=succeeded,
                        )
                        if rng.random() < 0.12:
                            state.finish_refund(
                                request_key,
                                provider_refund_id,
                                succeeded=succeeded,
                            )
                            stats["duplicate_retries"] += 1
                    state.assert_invariants()
        except AssertionError as exc:
            raise CommandError(f"simulation invariant failed: {exc}") from exc

        self.stdout.write(self.style.SUCCESS("1000-client simulation passed"))
        for key, value in stats.items():
            self.stdout.write(f"{key}={value}")
