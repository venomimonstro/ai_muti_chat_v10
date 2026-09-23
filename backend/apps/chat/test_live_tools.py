from apps.chat.live_tools import (
    is_time_query,
    is_weather_query,
    live_context,
    needs_web_search,
)


def test_time_intent_is_detected_without_router_keywords():
    assert is_time_query("Сколько сейчас времени?")
    assert is_time_query("Который час в Москве?")


def test_weather_intent_is_detected_without_router_keywords():
    assert is_weather_query("Какая сейчас погода в Москве?")
    assert is_weather_query("Температура в Сочи сегодня")


def test_fresh_web_search_intents_cover_yandex_and_explicit_search():
    assert needs_web_search("Найди в интернете актуальные данные по рынку")
    assert needs_web_search("Проверь это в Яндекс поисковой выдаче")
    assert needs_web_search("Какие последние новости по теме?")


def test_weather_without_location_fails_closed_and_asks_for_city():
    handled, context, metadata = live_context("Какая сейчас погода?")
    assert handled is True
    assert metadata == {"kind": "weather", "status": "needs_location"}
    assert "указать город" in context
    assert "Не придумывай" in context


def test_clock_without_location_uses_configured_server_timezone():
    handled, context, metadata = live_context("Сколько сейчас времени?")
    assert handled is True
    assert metadata["kind"] == "time"
    assert metadata["status"] == "ok"
    assert metadata["timezone"]
    assert metadata["time"] in context
