from decimal import Decimal

import pytest

from . import product_identity
from .live_tools import is_time_query, needs_web_search


@pytest.mark.parametrize(
    "query",
    [
        "какой курс доллар рубля сегодня",
        "какая стоимость билета на самолёте Москва Сочи сегодня",
        "сколько стоит авиабилет Москва Сочи на сегодня",
        "актуальная цена билета Москва Сочи",
    ],
)
def test_current_commercial_queries_require_web_search(query):
    assert needs_web_search(query) is True


def test_exact_time_uses_live_tool_instead_of_general_web_search():
    query = "сколько времени сейчас"
    assert is_time_query(query) is True
    assert needs_web_search(query) is False


def test_exact_time_can_answer_without_llm(monkeypatch):
    monkeypatch.setattr(
        product_identity,
        "current_time",
        lambda place="": {
            "place": "Москва",
            "timezone": "Europe/Moscow",
            "date": "27.09.2026",
            "time": "21:30:00",
        },
    )

    answer = product_identity.direct_identity_answer("сколько времени сейчас")

    assert "21:30:00" in answer
    assert "Europe/Moscow" in answer


def test_official_usd_rate_can_answer_without_llm(monkeypatch):
    monkeypatch.setattr(
        product_identity,
        "_official_cbr_rate",
        lambda code: {
            "code": code,
            "name": "Доллар США",
            "rate": Decimal("82.1234"),
            "date": "27.09.2026",
            "source": "https://www.cbr.ru/scripts/XML_daily.asp",
        },
    )

    answer = product_identity.direct_identity_answer("какой курс доллар рубля сегодня")

    assert "82,1234" in answer
    assert "Банк России" in answer
    assert "27.09.2026" in answer


def test_multi_current_topic_does_not_swallow_other_part(monkeypatch):
    monkeypatch.setattr(
        product_identity,
        "current_time",
        lambda place="": {
            "place": "Москва",
            "timezone": "Europe/Moscow",
            "date": "27.09.2026",
            "time": "21:30:00",
        },
    )

    answer = product_identity.direct_identity_answer(
        "сколько времени сейчас и какой курс доллар рубля сегодня"
    )

    assert answer is None
