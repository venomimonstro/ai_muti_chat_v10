from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from .limits import agent_commercial_snapshot
from .models import Agent


class AgentUsageView(APIView):
    def get(self, request, agent_id):
        agent = get_object_or_404(Agent.objects.select_related("owner"), id=agent_id, owner=request.user)
        snapshot = agent_commercial_snapshot(agent)
        return Response(
            {
                "agent_id": str(agent.id),
                "currency": "RUB",
                "run": {
                    "spent": snapshot["run_spend"],
                    "limit": snapshot["run_limit"],
                    "remaining": snapshot["run_remaining"],
                },
                "day": {
                    "spent": snapshot["day_spend"],
                    "limit": snapshot["day_limit"],
                    "remaining": snapshot["day_remaining"],
                },
                "month": {
                    "spent": snapshot["month_spend"],
                    "limit": snapshot["month_limit"],
                    "remaining": snapshot["month_remaining"],
                },
                "effective_remaining": snapshot["effective_remaining"],
                "concurrency": {
                    "active": snapshot["active_runs"],
                    "limit": snapshot["concurrency_limit"],
                    "available": snapshot["available_slots"],
                },
                "can_start": snapshot["can_start"],
            }
        )
