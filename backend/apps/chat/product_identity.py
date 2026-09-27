import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

import httpx
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.ai_registry.web_tools import WebToolError, _assert_public_http_url
from apps.workspace_search.embeddings import index_message

from .branches import ensure_active_branch
from .live_tools import LiveToolError, _extract_location, current_time, current_weather, is_time_query, is_weather_query
from .models import Conversation, Generation, Message

logger = logging.getLogger(__name__)
ZERO = Decimal("0.0000")
PUBLIC_SYSTEM_LEVELS = {
    Conversation.RoutingMode.ECONOMY: "System Lite",
    Conversation.RoutingMode.BALANCED: "System Pro",
    Conversation.RoutingMode.MAXIMUM: "System Max",
}

IDENTITY_QUESTIONS = {
    "кто ты",
    "ты кто",
    "кто ты такой",
    "кто ты такая",
    "представься",
}
CREATOR_QUESTIONS = {
    "кто тебя создал",
    "кто тебя разработал",
    "кто твой создатель",
    "кто твой разработчик",
    "кем ты создан",
    "кем ты создана",
}
CBR_DAILY_URL = "https://www.cbr.ru/scripts/XML_daily.asp"


def _normalize_question(value):
    value = str(value or "").casefold().replace("ё", "е")
    value = re.sub(r"[^0-9a-zа-я]+", " ", value, flags=re.IGNORECASE)
    return " ".join(value.split())


def _currency_code(query: str) -> str:
    text = _normalize_question(query)
    if "курс" not in text and "руб" not in text:
        return ""
    if any(token in text for token in ("доллар", "доллара", "доллару", "usd")):
        return "USD"
    if any(token in text for token in ("евро", "eur")):
        return "EUR"
    return ""


def _official_cbr_rate(code: str) -> dict:
    endpoint = os.getenv("CBR_DAILY_RATES_URL", CBR_DAILY_URL).strip()
    try:
        _assert_public_http_url(endpoint)
    except WebToolError as exc:
        raise LiveToolError("Unsafe CBR endpoint") from exc
    try:
        response = httpx.get(
            endpoint,
            headers={"User-Agent": "AIWorkspace-LiveData/2.3", "Accept": "application/xml,text/xml"},
            timeout=max(2.0, min(float(os.getenv("LIVE_TOOL_TIMEOUT_SECONDS", "8")), 12.0)),
            follow_redirects=False,
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except (httpx.HTTPError, ET.ParseError, ValueError) as exc:
        raise LiveToolError("CBR currency source failed") from exc
    for node in root.findall("Valute"):
        if (node.findtext("CharCode") or "").strip().upper() != code:
            continue
        try:
            nominal = Decimal((node.findtext("Nominal") or "1").replace(",", "."))
            value = Decimal((node.findtext("Value") or "").replace(",", "."))
            rate = value / nominal
        except (InvalidOperation, ZeroDivisionError) as exc:
            raise LiveToolError("CBR currency source returned invalid rate") from exc
        return {
            "code": code,
            "name": (node.findtext("Name") or code).strip(),
            "rate": rate,
            "date": str(root.attrib.get("Date") or "").strip(),
            "source": endpoint,
        }
    raise LiveToolError(f"CBR currency {code} not found")


def _direct_kind(content: str) -> str:
    normalized = _normalize_question(content)
    if normalized in IDENTITY_QUESTIONS or normalized in CREATOR_QUESTIONS:
        return "product_identity"
    if is_weather_query(content):
        return "live_weather"
    if is_time_query(content):
        return "live_time"
    if _currency_code(content):
        return "live_fx"
    return ""


def direct_identity_answer(content, file_ids=None):
    """Return deterministic zero-cost answers that should not depend on an LLM.

    The historical function name is kept for API compatibility; it now also
    covers exact time, current weather and official CBR FX rates.
    """
    if file_ids:
        return None
    normalized = _normalize_question(content)
    if normalized in IDENTITY_QUESTIONS:
        return "Я ваш агент."
    if normalized in CREATOR_QUESTIONS:
        return "Компания BBTEC."

    # Do not swallow multi-part research requests. If a prompt asks for time plus
    # another current topic, the normal grounded-chat pipeline should answer all parts.
    currency = _currency_code(content)
    current_topic_count = int(is_time_query(content)) + int(is_weather_query(content)) + int(bool(currency))
    if current_topic_count > 1:
        return None

    if is_time_query(content):
        place = _extract_location(content)
        try:
            data = current_time(place)
        except LiveToolError:
            return "Не удалось получить точное текущее время из live-источника. Попробуйте ещё раз через несколько секунд."
        return (
            f"Сейчас в **{data['place']} — {data['time']}**, {data['date']}.\n\n"
            f"Часовой пояс: `{data['timezone']}`."
        )

    if is_weather_query(content):
        place = _extract_location(content)
        if not place:
            return "Укажите город, для которого нужна текущая погода."
        try:
            data = current_weather(place)
        except LiveToolError:
            return "Не удалось получить текущую погоду из live-источника. Попробуйте ещё раз через несколько секунд."
        location = data["place"] + (f", {data['country']}" if data.get("country") else "")
        return (
            f"Сейчас в **{location}**: **{data['temperature_c']} °C**, {data['condition']}.\n\n"
            f"- Ощущается как: {data['apparent_temperature_c']} °C\n"
            f"- Влажность: {data['humidity_percent']}%\n"
            f"- Ветер: {data['wind_kmh']} км/ч"
            + (f", порывы до {data['wind_gusts_kmh']} км/ч" if data.get("wind_gusts_kmh") is not None else "")
            + f"\n- Наблюдение: {data['observed_at']} ({data['timezone']})"
        )

    if currency:
        try:
            data = _official_cbr_rate(currency)
        except LiveToolError:
            return None  # General free web-search can still answer with current sources.
        formatted = f"{data['rate']:.4f}".replace(".", ",")
        return (
            f"Официальный курс **{currency}/RUB — {formatted} ₽ за 1 {currency}**.\n\n"
            f"Дата курса Банка России: **{data['date'] or 'последняя опубликованная'}**. "
            f"Это официальный курс ЦБ, он может отличаться от биржевого курса и курса покупки/продажи в банках.\n\n"
            f"Источник: [Банк России]({data['source']})"
        )
    return None


def public_system_level(conversation):
    return PUBLIC_SYSTEM_LEVELS.get(conversation.routing_mode, "System Pro")


def identity_preview(conversation):
    level = public_system_level(conversation)
    return {
        "estimated_min_rub": ZERO,
        "estimated_max_rub": ZERO,
        "confirmation_required": False,
        "confirmation_threshold_rub": ZERO,
        "selected_model": level,
        "models": [{"model": level, "display_name": level, "estimated_max_rub": "0.0000"}],
        "spend_guard": {},
        "blocked_by_spend_guard": False,
        "spend_guard_message": "",
    }


def _validate_existing(generation, *, conversation, content, client_message_id):
    message = generation.user_message
    snapshot = generation.context_snapshot or {}
    if (
        message.conversation_id != conversation.id
        or message.content != content
        or message.client_message_id != client_message_id
        or not (snapshot.get("local_direct_answer") or snapshot.get("local_product_identity"))
    ):
        raise ValidationError("Idempotency-Key уже использован для другого запроса")
    return generation


def create_identity_generation(
    *, user, conversation, content, client_message_id, idempotency_key, answer
):
    """Persist a deterministic zero-cost answer with the same replay guarantees as chat."""
    existing = (
        Generation.objects.filter(owner=user, idempotency_key=idempotency_key)
        .select_related("user_message", "assistant_message")
        .first()
    )
    if existing is not None:
        return _validate_existing(
            existing,
            conversation=conversation,
            content=content,
            client_message_id=client_message_id,
        ), False

    with transaction.atomic():
        user.__class__.objects.select_for_update().only("pk").get(pk=user.pk)
        existing = (
            Generation.objects.filter(owner=user, idempotency_key=idempotency_key)
            .select_related("user_message", "assistant_message")
            .first()
        )
        if existing is not None:
            return _validate_existing(
                existing,
                conversation=conversation,
                content=content,
                client_message_id=client_message_id,
            ), False

        locked = Conversation.objects.select_for_update().get(pk=conversation.pk, owner=user)
        repeated = (
            Message.objects.filter(
                conversation=locked,
                client_message_id=client_message_id,
                role=Message.Role.USER,
            )
            .select_related("generation_request__assistant_message")
            .first()
        )
        if repeated is not None:
            if repeated.content != content:
                raise ValidationError("client_message_id уже использован с другим содержимым")
            try:
                generation = repeated.generation_request
            except Message.generation_request.RelatedObjectDoesNotExist:
                raise ValidationError("Повторное сообщение ещё не готово к обработке") from None
            return _validate_existing(
                generation,
                conversation=locked,
                content=content,
                client_message_id=client_message_id,
            ), False

        branch = ensure_active_branch(locked, user)
        user_message = Message.objects.create(
            conversation=locked,
            branch=branch,
            role=Message.Role.USER,
            content=content,
            client_message_id=client_message_id,
            status=Message.Status.SAVED,
        )
        assistant = Message.objects.create(
            conversation=locked,
            branch=branch,
            role=Message.Role.ASSISTANT,
            content=answer,
            status=Message.Status.COMPLETED,
        )
        level = public_system_level(locked)
        direct_kind = _direct_kind(content) or "local_direct"
        routing = {
            "decision_id": "",
            "mode": locked.routing_mode,
            "task_taxonomy": direct_kind,
            "selected_model": level,
            "model_version": level,
            "exact_api_id": "",
            "explanation": "Ответ получен из проверенного локального/live-инструмента без вызова генеративной модели.",
            "policy_version": "local-direct-v2",
            "classification_confidence": 1.0,
            "required_capabilities": ["live_data"] if direct_kind.startswith("live_") else [],
            "estimated_cost_rub": "0.0000",
            "candidates": [],
        }
        context_snapshot = {
            "local_direct_answer": direct_kind,
            "local_product_identity": direct_kind == "product_identity",
            "routing": routing,
        }
        generation = Generation.objects.create(
            owner=user,
            user_message=user_message,
            assistant_message=assistant,
            state=Generation.State.COMPLETED,
            model=level,
            routed_model=level,
            provider_slug="system",
            idempotency_key=idempotency_key,
            actual_cost_rub=ZERO,
            completed_at=timezone.now(),
            context_snapshot=context_snapshot,
        )

    for message in (user_message, assistant):
        try:
            index_message(message)
        except Exception:
            logger.exception("Direct-answer history indexing failed message_id=%s", message.id)
    return generation, True


def identity_sse(generation):
    text = generation.assistant_message.content
    level = generation.model or "System Pro"
    yield (
        "event: generation\n"
        f'data: {{"id":"{generation.id}","state":"streaming","correlation_id":"{generation.correlation_id}"}}\n\n'
    )
    kind = str((generation.context_snapshot or {}).get("local_direct_answer") or "product_identity")
    if kind.startswith("live_"):
        yield "event: research_progress\ndata: " + json.dumps(
            {"phase": "live_data", "message": "Получил проверенные live-данные. Формирую ответ…"},
            ensure_ascii=False,
        ) + "\n\n"
    yield f"event: delta\ndata: {json.dumps({'text': text}, ensure_ascii=False)}\n\n"
    yield "event: completed\ndata: " + json.dumps(
        {
            "state": "completed",
            "cost_rub": "0.0000",
            "input_tokens": 0,
            "output_tokens": 0,
            "model": level,
            "model_version": level,
            "provider": "system",
        },
        ensure_ascii=False,
    ) + "\n\n"
