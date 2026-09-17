from types import SimpleNamespace

import pytest

from apps.ai_registry.router import classify_task
from apps.evals.models import EvalCase


@pytest.mark.django_db
def test_classifies_coding_without_llm_call():
    conversation = SimpleNamespace(project_id=None)
    result = classify_task("Напиши код Python API и исправь ошибку traceback", conversation)
    assert result.taxonomy in {EvalCase.Taxonomy.CODING, EvalCase.Taxonomy.DEBUGGING}
    assert result.confidence >= 0.58
    assert "text" in result.required_capabilities


@pytest.mark.django_db
def test_marks_fresh_research_as_tool_need():
    conversation = SimpleNamespace(project_id=None)
    result = classify_task("Найди актуальные последние новости сегодня", conversation)
    assert result.signals["needs_tools"] is True


@pytest.mark.django_db
def test_long_context_uses_token_signal():
    conversation = SimpleNamespace(project_id=None)
    result = classify_task("обычный текст " * 5000, conversation)
    assert result.signals["long_context"] is True
    assert result.signals["content_tokens"] > 2000
