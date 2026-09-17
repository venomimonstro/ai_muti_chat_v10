import os
import threading

from fastembed import TextEmbedding
from fastembed.common.model_description import ModelSource, PoolingType

MODEL_NAME = os.getenv("RAG_SEMANTIC_MODEL", "intfloat/multilingual-e5-small")
MODEL_VERSION = f"fastembed:{MODEL_NAME}:v1"
_DIMENSIONS = 384
_lock = threading.Lock()
_model = None
_registered = False


def _build_model():
    global _registered
    if MODEL_NAME == "intfloat/multilingual-e5-small" and not _registered:
        TextEmbedding.add_custom_model(
            model=MODEL_NAME,
            pooling=PoolingType.MEAN,
            normalization=True,
            sources=ModelSource(hf=MODEL_NAME),
            dim=_DIMENSIONS,
            model_file="onnx/model.onnx",
        )
        _registered = True
    return TextEmbedding(model_name=MODEL_NAME)


def model():
    global _model
    if _model is not None:
        return _model
    with _lock:
        if _model is None:
            _model = _build_model()
    return _model


def _embed(value: str, *, prefix: str) -> list[float]:
    text = " ".join((value or "").split())
    if not text:
        return [0.0] * _DIMENSIONS
    payload = f"{prefix}: {text}"
    vector = next(iter(model().embed([payload])))
    values = [float(item) for item in vector]
    if len(values) != _DIMENSIONS:
        raise RuntimeError(f"Unexpected embedding dimensions: {len(values)}")
    return values


def embed_passage(value: str) -> list[float]:
    return _embed(value, prefix="passage")


def embed_query(value: str) -> list[float]:
    return _embed(value, prefix="query")
