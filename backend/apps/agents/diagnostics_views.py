from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.connections.models import AgentConnectionBinding

from .limits import agent_commercial_snapshot
from .models import Agent, AgentRun
from .readiness import agent_readiness


class AgentDiagnosticsView(APIView):
    """Return user-facing causes and next actions without exposing internal secrets."""

    def get(self, request, agent_id):
        agent = get_object_or_404(
            Agent.objects.filter(owner=request.user).select_related("project", "owner"),
            id=agent_id,
        )
        readiness = agent_readiness(agent)
        commercial = agent_commercial_snapshot(agent)
        bindings = list(
            AgentConnectionBinding.objects.filter(agent=agent, enabled=True)
            .select_related("connection")
            .order_by("purpose", "created_at")
        )
        recent = list(
            AgentRun.objects.filter(owner=request.user, agent=agent)
            .order_by("-created_at")
            .values("id", "state", "error_code", "error_message", "created_at")[:5]
        )

        issues = []
        actions = []
        for blocker in readiness.get("blockers") or []:
            issues.append({"severity": "blocker", "code": "readiness", "message": str(blocker)})
        for warning in readiness.get("warnings") or []:
            issues.append({"severity": "warning", "code": "readiness_warning", "message": str(warning)})

        if commercial["effective_remaining"] <= 0:
            issues.append(
                {
                    "severity": "blocker",
                    "code": "agent_budget_exhausted",
                    "message": "Достигнут денежный лимит агента. Новый платный шаг не начнётся.",
                }
            )
            actions.append({"code": "review_budget", "label": "Проверить бюджет агента", "href": f"/app/agents/{agent.id}"})
        if commercial["available_slots"] <= 0:
            issues.append(
                {
                    "severity": "blocker",
                    "code": "concurrency_limit",
                    "message": "Все доступные слоты одновременных задач заняты.",
                }
            )
            actions.append({"code": "open_runs", "label": "Открыть активные задачи", "href": "/app/runs"})

        for binding in bindings:
            connection = binding.connection
            if not connection.enabled:
                issues.append({"severity": "blocker", "code": "connection_disabled", "message": f"Подключение «{connection.name}» отключено."})
            elif connection.health_state == connection.Health.DEGRADED:
                issues.append({"severity": "blocker", "code": "connection_degraded", "message": f"Подключение «{connection.name}» требует повторной проверки."})
                actions.append({"code": f"check_connection:{connection.id}", "label": f"Проверить {connection.name}", "href": f"/app/agents/{agent.id}"})
            elif connection.health_state == connection.Health.UNKNOWN:
                issues.append({"severity": "warning", "code": "connection_unchecked", "message": f"Подключение «{connection.name}» ещё не проверено."})

        latest_failure = next((row for row in recent if row["state"] in {AgentRun.State.FAILED, AgentRun.State.BUDGET_EXCEEDED}), None)
        if latest_failure:
            issues.append(
                {
                    "severity": "info",
                    "code": str(latest_failure.get("error_code") or "last_run_failed"),
                    "message": str(latest_failure.get("error_message") or "Последний запуск завершился ошибкой")[:1000],
                    "run_id": str(latest_failure["id"]),
                }
            )
            actions.append({"code": "open_failed_run", "label": "Открыть последний проблемный запуск", "href": f"/app/runs/{latest_failure['id']}"})

        if not issues:
            issues.append({"severity": "ok", "code": "healthy", "message": "Явных проблем не найдено. Агент готов к тестовому или реальному запуску."})

        return Response(
            {
                "agent_id": str(agent.id),
                "ready": bool(readiness.get("ready")) and commercial["can_start"],
                "issues": issues,
                "actions": actions,
                "commercial": {
                    "effective_remaining": commercial["effective_remaining"],
                    "active_runs": commercial["active_runs"],
                    "concurrency_limit": commercial["concurrency_limit"],
                },
                "connections": [
                    {
                        "id": str(item.connection_id),
                        "name": item.connection.name,
                        "kind": item.connection.kind,
                        "purpose": item.purpose,
                        "health": item.connection.health_state,
                    }
                    for item in bindings
                ],
            }
        )
