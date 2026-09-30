import json

import pytest
from django.core.exceptions import ValidationError

from .dev_plan import fallback_dev_plan, parse_director_plan, plan_for_run, plan_rows_for_ui


def _text(tasks, summary="plan"):
    return "Director result\n```json\n" + json.dumps(
        {"dev_plan": {"summary": summary, "tasks": tasks}}, ensure_ascii=False
    ) + "\n```"


def test_director_plan_is_topologically_ordered():
    plan = parse_director_plan(
        _text(
            [
                {
                    "id": "dev",
                    "title": "Implement",
                    "role": "Development",
                    "depends_on": ["arch"],
                    "acceptance": "tests",
                },
                {
                    "id": "arch",
                    "title": "Inspect",
                    "role": "Architecture",
                    "depends_on": [],
                    "acceptance": "plan",
                },
                {
                    "id": "qa",
                    "title": "Verify",
                    "role": "QA & Security",
                    "depends_on": ["dev"],
                    "acceptance": "green",
                },
            ]
        )
    )
    assert [task["id"] for task in plan["tasks"]] == ["arch", "dev", "qa"]


def test_director_plan_rejects_cycle():
    with pytest.raises(ValidationError, match="циклический"):
        parse_director_plan(
            _text(
                [
                    {"id": "a", "title": "A", "role": "Development", "depends_on": ["b"]},
                    {"id": "b", "title": "B", "role": "Development", "depends_on": ["a"]},
                ]
            )
        )


def test_director_plan_rejects_unknown_dependency():
    with pytest.raises(ValidationError, match="отсутствующей"):
        parse_director_plan(
            _text(
                [
                    {
                        "id": "dev",
                        "title": "Implement",
                        "role": "Development",
                        "depends_on": ["missing"],
                    }
                ]
            )
        )


def test_director_plan_rejects_unapproved_role():
    with pytest.raises(ValidationError, match="неподдерживаемую роль"):
        parse_director_plan(
            _text(
                [
                    {
                        "id": "ops",
                        "title": "Do everything",
                        "role": "Root Shell",
                        "depends_on": [],
                    }
                ]
            )
        )


def test_missing_machine_plan_uses_safe_fallback():
    plan = plan_for_run("plain narrative without JSON")
    assert plan == fallback_dev_plan()
    assert [task["role"] for task in plan["tasks"]] == [
        "Architecture",
        "Development",
        "QA & Security",
    ]


def test_ui_plan_adds_protected_system_stages():
    rows = plan_rows_for_ui(fallback_dev_plan())
    ids = [row["id"] for row in rows]
    assert "approval" in ids
    assert "sandbox-write" in ids
    assert "final" in ids
