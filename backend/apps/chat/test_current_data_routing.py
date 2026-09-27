import pytest

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
