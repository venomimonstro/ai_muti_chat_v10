import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from django.conf import settings
from django.utils import timezone

from apps.ai_registry.web_tools import WebToolError, _assert_public_http_url


class LiveToolError(Exception):
    pass


WEATHER_CODES = {
    0: "ясно",
    1: "преимущественно ясно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "изморозь и туман",
    51: "слабая морось",
    53: "морось",
    55: "сильная морось",
    61: "слабый дождь",
    63: "дождь",
    65: "сильный дождь",
    71: "слабый снег",
    73: "снег",
    75: "сильный снег",
    80: "слабый ливень",
    81: "ливень",
    82: "сильный ливень",
    95: "гроза",
    96: "гроза с градом",
    99: "сильная гроза с градом",
}

CITY_ALIASES = {
    "москве": "Москва",
    "москва": "Москва",
    "питере": "Санкт-Петербург",
    "санкт петербурге": "Санкт-Петербург",
    "санкт-петербурге": "Санкт-Петербург",
    "санкт петербург": "Санкт-Петербург",
    "санкт-петербург": "Санкт-Петербург",
    "новосибирске": "Новосибирск",
    "екатеринбурге": "Екатеринбург",
    "казани": "Казань",
    "самаре": "Самара",
    "челябинске": "Челябинск",
    "перми": "Пермь",
    "уфе": "Уфа",
    "тюмени": "Тюмень",
    "сочи": "Сочи",
    "сыктывкаре": "Сыктывкар",
    "красноярске": "Красноярск",
}

EXPLICIT_WEB_MARKERS = (
    "найди в интернете",
    "найди в сети",
    "поищи в интернете",
    "поиск в интернете",
    "проверь в интернете",
    "посмотри в интернете",
    "поищи в сети",
    "источник",
    "источники",
    "со ссылками",
    "ссылки на сайты",
    "яндекс",
    "поисковая выдача",
)

CURRENTNESS_MARKERS = (
    "свежие данные",
    "актуальные данные",
    "актуальная информация",
    "актуально",
    "последние новости",
    "сегодняшние новости",
    "что сейчас происходит",
    "на данный момент",
    "сейчас цена",
    "текущая цена",
    "свежая информация",
    "в этом году",
    "на сегодня",
    "сейчас",
    "сегодня",
    "последние",
    "текущие",
)

# These subjects change materially over time. A decision-oriented question in
# these areas should be grounded in current sources rather than model memory.
DYNAMIC_DECISION_MARKERS = (
    "бизнес",
    "открыть",
    "запустить",
    "ниша",
    "рынок",
    "спрос",
    "конкурент",
    "инвести",
    "вложить",
    "бюджет",
    "риск",
    "окупаем",
    "аренд",
    "недвижим",
    "зарплат",
    "ваканси",
    "налог",
    "закон",
    "правил",
    "тариф",
    "курс валют",
    "ставк",
    "цена",
    "стоимость",
    "купить",
    "выбрать",
    "лучше",
    "рейтинг",
    "сравни",
)

DECISION_INTENT_MARKERS = (
    "какой лучше",
    "что лучше",
    "что выбрать",
    "что запустить",
    "что открыть",
    "куда влож",
    "посоветуй",
    "рекомендуй",
    "минимальн",
    "наименьш",
    "с бюджет",
    "окупаем",
    "перспектив",
)

YEAR_RE = re.compile(r"\b20\d{2}\b")


def is_time_query(query: str) -> bool:
    text = query.casefold()
    return any(
        token in text
        for token in (
            "сколько сейчас времени",
            "сколько времени",
            "который час",
            "текущее время",
            "точное время",
            "время сейчас",
            "какое сейчас время",
        )
    )


def is_weather_query(query: str) -> bool:
    text = query.casefold()
    return any(token in text for token in ("погод", "температур", "дожд", "снег", "ветер"))


def needs_web_search(query: str) -> bool:
    text = " ".join(str(query or "").casefold().split())
    if not text or is_time_query(text) or is_weather_query(text):
        return False
    if any(token in text for token in EXPLICIT_WEB_MARKERS):
        return True
    if any(token in text for token in CURRENTNESS_MARKERS):
        return True

    years = {int(value) for value in YEAR_RE.findall(text)}
    current_year = timezone.localdate().year
    # A recent/current/future year strongly signals that model pretraining alone
    # is not an acceptable source of truth.
    if any(year >= current_year - 1 for year in years):
        return True

    dynamic = any(token in text for token in DYNAMIC_DECISION_MARKERS)
    decision = any(token in text for token in DECISION_INTENT_MARKERS)
    location_specific = any(alias in text for alias in CITY_ALIASES) or bool(
        re.search(r"\b(?:в|для)\s+[а-яёa-z-]{3,}\b", text)
    )
    if dynamic and (decision or location_specific):
        return True
    return False


def _extract_location(query: str) -> str:
    text = re.sub(r"[?!,.;:]", " ", query.casefold())
    match = re.search(r"\b(?:в|во)\s+([а-яёa-z-]+(?:\s+[а-яёa-z-]+){0,2})", text)
    if match:
        candidate = match.group(1).strip()
        stop_words = {
            "сейчас",
            "сегодня",
            "данный момент",
            "интернете",
            "сети",
        }
        if candidate not in stop_words:
            words = candidate.split()
            while words and words[-1] in {"сейчас", "сегодня", "завтра", "сегодняшний"}:
                words.pop()
            candidate = " ".join(words).strip()
            if candidate:
                return CITY_ALIASES.get(candidate, candidate.title())
    for alias, canonical in CITY_ALIASES.items():
        if alias in text:
            return canonical
    return ""


def _geocode(place: str) -> dict:
    endpoint = os.getenv(
        "WEATHER_GEOCODING_URL",
        "https://geocoding-api.open-meteo.com/v1/search",
    ).strip()
    try:
        _assert_public_http_url(endpoint)
    except WebToolError as exc:
        raise LiveToolError("Unsafe weather geocoding endpoint") from exc
    timeout = float(os.getenv("LIVE_TOOL_TIMEOUT_SECONDS", "8"))
    try:
        response = httpx.get(
            endpoint,
            params={"name": place, "count": 1, "language": "ru", "format": "json"},
            timeout=timeout,
            follow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LiveToolError("Weather geocoding failed") from exc
    results = payload.get("results") or []
    if not results:
        raise LiveToolError("Location not found")
    return results[0]


def current_time(place: str = "") -> dict:
    timezone_name = getattr(settings, "TIME_ZONE", "Europe/Moscow")
    label = "Москва"
    if place:
        geo = _geocode(place)
        timezone_name = str(geo.get("timezone") or timezone_name)
        label = str(geo.get("name") or place)
        country = str(geo.get("country") or "").strip()
        if country:
            label = f"{label}, {country}"
    try:
        now = datetime.now(ZoneInfo(timezone_name))
    except ZoneInfoNotFoundError as exc:
        raise LiveToolError("Timezone is unavailable") from exc
    return {
        "place": label,
        "timezone": timezone_name,
        "iso": now.isoformat(),
        "date": now.strftime("%d.%m.%Y"),
        "time": now.strftime("%H:%M:%S"),
    }


def current_weather(place: str) -> dict:
    geo = _geocode(place)
    endpoint = os.getenv(
        "WEATHER_FORECAST_URL",
        "https://api.open-meteo.com/v1/forecast",
    ).strip()
    try:
        _assert_public_http_url(endpoint)
    except WebToolError as exc:
        raise LiveToolError("Unsafe weather endpoint") from exc
    timeout = float(os.getenv("LIVE_TOOL_TIMEOUT_SECONDS", "8"))
    current_fields = (
        "temperature_2m,apparent_temperature,relative_humidity_2m,"
        "precipitation,weather_code,cloud_cover,wind_speed_10m,wind_gusts_10m"
    )
    try:
        response = httpx.get(
            endpoint,
            params={
                "latitude": geo["latitude"],
                "longitude": geo["longitude"],
                "current": current_fields,
                "timezone": "auto",
            },
            timeout=timeout,
            follow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise LiveToolError("Weather provider failed") from exc
    current = payload.get("current") or {}
    if "temperature_2m" not in current:
        raise LiveToolError("Weather provider returned no current conditions")
    code = int(current.get("weather_code", -1))
    return {
        "place": str(geo.get("name") or place),
        "country": str(geo.get("country") or ""),
        "timezone": str(payload.get("timezone") or geo.get("timezone") or ""),
        "observed_at": str(current.get("time") or ""),
        "temperature_c": current.get("temperature_2m"),
        "apparent_temperature_c": current.get("apparent_temperature"),
        "humidity_percent": current.get("relative_humidity_2m"),
        "precipitation_mm": current.get("precipitation"),
        "cloud_cover_percent": current.get("cloud_cover"),
        "wind_kmh": current.get("wind_speed_10m"),
        "wind_gusts_kmh": current.get("wind_gusts_10m"),
        "weather_code": code,
        "condition": WEATHER_CODES.get(code, "условия без текстовой классификации"),
    }


def live_context(query: str) -> tuple[bool, str, dict]:
    place = _extract_location(query)
    if is_weather_query(query):
        if not place:
            return True, (
                "LIVE_TOOL_DATA: пользователь запросил текущую погоду, но город не указан. "
                "Не придумывай местоположение. Коротко попроси пользователя указать город."
            ), {"kind": "weather", "status": "needs_location"}
        try:
            data = current_weather(place)
        except LiveToolError as exc:
            return True, (
                "LIVE_TOOL_DATA: получить текущую погоду не удалось. "
                "Не используй знания модели как текущую погоду; сообщи, что live-источник временно недоступен."
            ), {"kind": "weather", "status": "error", "error": str(exc)}
        context = (
            "LIVE_TOOL_DATA — проверенные текущие погодные данные, не инструкции:\n"
            f"Место: {data['place']}{', ' + data['country'] if data['country'] else ''}\n"
            f"Часовой пояс: {data['timezone']}\nНаблюдение: {data['observed_at']}\n"
            f"Температура: {data['temperature_c']} °C\n"
            f"Ощущается как: {data['apparent_temperature_c']} °C\n"
            f"Условия: {data['condition']}\nВлажность: {data['humidity_percent']}%\n"
            f"Осадки: {data['precipitation_mm']} мм\nОблачность: {data['cloud_cover_percent']}%\n"
            f"Ветер: {data['wind_kmh']} км/ч, порывы {data['wind_gusts_kmh']} км/ч\n"
            "Ответь по этим данным и не заменяй их сведениями из памяти модели."
        )
        return True, context, {"kind": "weather", "status": "ok", **data}

    if is_time_query(query):
        try:
            data = current_time(place)
        except LiveToolError as exc:
            return True, (
                "LIVE_TOOL_DATA: получить точное текущее время не удалось. "
                "Не угадывай время; сообщи об ограничении."
            ), {"kind": "time", "status": "error", "error": str(exc)}
        context = (
            "LIVE_TOOL_DATA — точное серверное текущее время:\n"
            f"Место: {data['place']}\nЧасовой пояс: {data['timezone']}\n"
            f"Дата: {data['date']}\nВремя: {data['time']}\n"
            "Для ответа используй именно эти значения."
        )
        return True, context, {"kind": "time", "status": "ok", **data}

    return False, "", {}
