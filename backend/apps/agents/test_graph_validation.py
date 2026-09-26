import pytest
from rest_framework.exceptions import ValidationError

from .config_views import _validate_graph


def _nodes():
    return [
        {"id": "start", "title": "Старт", "type": "llm", "prompt": "Сделай работу"},
        {"id": "review", "title": "Проверка", "type": "review", "prompt": "Проверь результат"},
        {"id": "finish", "title": "Готово", "type": "finish"},
    ]


def test_graph_accepts_one_forward_edge_per_step():
    graph = _validate_graph(
        {
            "version": 1,
            "nodes": _nodes(),
            "edges": [
                {"from": "start", "to": "review"},
                {"from": "review", "to": "finish"},
            ],
        }
    )

    assert graph["edges"] == [
        {"from": "start", "to": "review"},
        {"from": "review", "to": "finish"},
    ]


def test_graph_rejects_duplicate_edge():
    with pytest.raises(ValidationError, match="указана дважды"):
        _validate_graph(
            {
                "nodes": _nodes(),
                "edges": [
                    {"from": "start", "to": "review"},
                    {"from": "start", "to": "review"},
                ],
            }
        )


def test_graph_rejects_two_normal_outgoing_edges():
    with pytest.raises(ValidationError, match="только один обычный переход"):
        _validate_graph(
            {
                "nodes": _nodes(),
                "edges": [
                    {"from": "start", "to": "review"},
                    {"from": "start", "to": "finish"},
                ],
            }
        )


def test_condition_branch_targets_must_point_forward():
    nodes = [
        {"id": "start", "title": "Старт", "type": "llm"},
        {
            "id": "check",
            "title": "Условие",
            "type": "condition",
            "condition_source": "previous_text",
            "operator": "contains",
            "value": "готово",
            "on_true": "start",
        },
        {"id": "finish", "title": "Готово", "type": "finish"},
    ]
    with pytest.raises(ValidationError, match="может вести только на более поздний шаг"):
        _validate_graph({"nodes": nodes, "edges": []})


def test_wait_is_capped_at_seven_days():
    nodes = [{"id": "wait", "title": "Подождать", "type": "wait", "wait_minutes": 10081}]
    with pytest.raises(ValidationError, match="от 1 минуты до 7 дней"):
        _validate_graph({"nodes": nodes, "edges": []})
