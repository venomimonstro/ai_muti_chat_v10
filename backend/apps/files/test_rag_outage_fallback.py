from types import SimpleNamespace

from . import rag


class Chunks(list):
    def exclude(self, **kwargs):
        return self

    def exists(self):
        return bool(self)


def test_empty_authorized_corpus_does_not_load_semantic_model(monkeypatch):
    monkeypatch.setattr(rag, "authorized_chunks", lambda *args: Chunks())
    def forbidden(query):
        raise AssertionError("empty corpus must not depend on embeddings")
    monkeypatch.setattr(rag, "embed_query", forbidden)
    assert rag.retrieve_project_chunks(user=None, project_id="project", query="test") == []


def test_semantic_outage_preserves_authorized_lexical_results(monkeypatch, settings):
    chunk = SimpleNamespace(content="доставка заказа", embedding_model=rag.MODEL_VERSION, position=0)
    monkeypatch.setattr(rag, "authorized_chunks", lambda *args: Chunks([chunk]))
    def unavailable(query):
        raise RuntimeError("embedding service unavailable")
    monkeypatch.setattr(rag, "embed_query", unavailable)
    monkeypatch.setattr(rag, "citation_for", lambda value: {"id": "authorized-chunk"})
    settings.SMART_CONTEXT_MIN_RELEVANCE = 0.01
    hits = rag.retrieve_project_chunks(user=None, project_id="project", query="доставка")
    assert len(hits) == 1
    assert hits[0].chunk is chunk
    assert hits[0].vector_score == 0
    assert hits[0].citation == {"id": "authorized-chunk"}
