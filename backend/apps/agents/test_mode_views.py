from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Agent
from .readiness import agent_readiness


SIDE_EFFECT_TYPES = {"publish", "notify"}
APPROVAL_TYPES = {"approval"}


class AgentTestModeView(APIView):
    """Simulate an agent workflow without LLM, billing or external side effects."""

    def post(self, request, agent_id):
        agent = get_object_or_404(
            Agent.objects.filter(owner=request.user).select_related("project"),
            id=agent_id,
        )
        readiness = agent_readiness(agent)
        graph = agent.graph if isinstance(agent.graph, dict) else {}
        nodes = list(graph.get("nodes") or [])
        edges = list(graph.get("edges") or [])

        simulated = []
        for index, raw in enumerate(nodes[: agent.max_steps], start=1):
            node = raw if isinstance(raw, dict) else {}
            node_type = str(node.get("type") or "llm").strip().lower()
            status = "would_run"
            note = "Шаг будет выполнен только при реальном запуске."
            if node_type in SIDE_EFFECT_TYPES:
                status = "blocked_in_test"
                note = "В тестовом режиме внешнее действие не выполняется."
            elif node_type in APPROVAL_TYPES:
                status = "approval_checkpoint"
                note = "Реальный запуск остановится здесь до подтверждения пользователя."
            simulated.append(
                {
                    "sequence": index,
                    "id": str(node.get("id") or f"step-{index}"),
                    "title": str(node.get("title") or f"Шаг {index}"),
                    "type": node_type,
                    "status": status,
                    "note": note,
                }
            )

        warnings = list(readiness.get("warnings") or [])
        if len(nodes) > agent.max_steps:
            warnings.append(
                f"Карта содержит {len(nodes)} шагов, но лимит агента — {agent.max_steps}. Лишние шаги не выполнятся."
            )

        return Response(
            {
                "agent_id": str(agent.id),
                "mode": "simulation",
                "paid_calls": 0,
                "external_actions": 0,
                "ready": bool(readiness.get("ready")),
                "blockers": list(readiness.get("blockers") or []),
                "warnings": warnings,
                "summary": {
                    "nodes": len(nodes),
                    "edges": len(edges),
                    "simulated_steps": len(simulated),
                    "max_steps": agent.max_steps,
                },
                "steps": simulated,
            }
        )
