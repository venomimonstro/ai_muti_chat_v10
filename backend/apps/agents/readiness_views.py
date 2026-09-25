from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Agent
from .readiness import agent_readiness


class AgentReadinessView(APIView):
    def get(self, request, agent_id):
        agent = get_object_or_404(Agent.objects.select_related("project"), id=agent_id, owner=request.user)
        return Response(agent_readiness(agent))
