from __future__ import annotations

import json
from decimal import Decimal

from .paid_search_billing import public_search_charge

MONEY_STEP = Decimal("0.0001")


def _d(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def _total(generation):
    llm = _d(generation.actual_cost_rub)
    search = public_search_charge(generation)
    return llm, search, (llm + search).quantize(MONEY_STEP)


def install(*, serializers_module, managed_stream_module) -> None:
    raw_generation = serializers_module.MessageSerializer.get_generation
    raw_publicize = managed_stream_module._publicize_sse_chunk
    if getattr(raw_generation, "_ai_workspace_search_cost_public", False):
        return

    def get_generation(self, obj):
        data = raw_generation(self, obj)
        if data is None:
            return None
        try:
            generation = obj.generation_response
            llm, search, total = _total(generation)
        except Exception:
            return data
        data["cost_rub"] = total
        data["cost_breakdown"] = {
            "llm_rub": str(llm.quantize(MONEY_STEP)),
            "search_rub": str(search.quantize(MONEY_STEP)),
            "total_rub": str(total),
        }
        return data

    def publicize(generation, chunk):
        chunk = raw_publicize(generation, chunk)
        if not isinstance(chunk, str) or not chunk.startswith("event: completed\n"):
            return chunk
        try:
            lines = chunk.splitlines()
            data_line = next(line for line in lines if line.startswith("data: "))
            payload = json.loads(data_line[6:])
            llm, search, total = _total(generation)
            payload["cost_rub"] = str(total)
            payload["cost_breakdown"] = {
                "llm_rub": str(llm.quantize(MONEY_STEP)),
                "search_rub": str(search.quantize(MONEY_STEP)),
                "total_rub": str(total),
            }
            return (
                "event: completed\n"
                f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
            )
        except Exception:
            return chunk

    get_generation._ai_workspace_search_cost_public = True
    serializers_module.MessageSerializer.get_generation = get_generation
    managed_stream_module._publicize_sse_chunk = publicize
