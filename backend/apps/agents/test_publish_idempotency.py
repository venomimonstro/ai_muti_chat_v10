import uuid
from types import SimpleNamespace

from .publish_runtime import _stable_publish_slug


def test_publish_slug_is_stable_for_same_run_and_node():
    run = SimpleNamespace(id=uuid.UUID("11111111-2222-3333-4444-555555555555"))
    step = SimpleNamespace(node_id="publish-main")
    node = {"slug": "seo-article"}

    first = _stable_publish_slug(run, step, node, "Ignored title")
    second = _stable_publish_slug(run, step, node, "Changed title")

    assert first == second
    assert first.startswith("seo-article-")
    assert "11111111" in first
    assert len(first) <= 200


def test_publish_slug_differs_between_runs():
    step = SimpleNamespace(node_id="publish-main")
    node = {}
    first = _stable_publish_slug(
        SimpleNamespace(id=uuid.UUID("11111111-2222-3333-4444-555555555555")),
        step,
        node,
        "Ежедневный отчёт",
    )
    second = _stable_publish_slug(
        SimpleNamespace(id=uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")),
        step,
        node,
        "Ежедневный отчёт",
    )

    assert first != second
