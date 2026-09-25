from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.memory_store.models import MemoryItem
from apps.memory_store.serializers import MemoryItemSerializer

from .memory import memory_items_for_agent
from .models import Agent


def _agent(request, agent_id):
    return get_object_or_404(Agent.objects.select_related("project"), id=agent_id, owner=request.user)


def _visible_item(agent, memory_id):
    ids = {item.id for item in memory_items_for_agent(agent)}
    if memory_id not in ids:
        raise ValidationError({"memory": "Эта запись не используется данным агентом"})
    return get_object_or_404(MemoryItem, id=memory_id, owner=agent.owner)


class AgentMemoryView(APIView):
    def get(self, request, agent_id):
        agent = _agent(request, agent_id)
        items = memory_items_for_agent(agent)
        return Response(
            {
                "enabled": (agent.memory_policy or {}).get("enabled", True) is not False,
                "project_enabled": (agent.memory_policy or {}).get("project", True) is not False,
                "project": str(agent.project_id) if agent.project_id else None,
                "items": MemoryItemSerializer(items, many=True, context={"request": request}).data,
            }
        )

    def post(self, request, agent_id):
        agent = _agent(request, agent_id)
        requested_scope = str(request.data.get("scope") or "").strip()
        scope = requested_scope or (MemoryItem.Scope.PROJECT if agent.project_id else MemoryItem.Scope.GLOBAL)
        if scope not in {MemoryItem.Scope.GLOBAL, MemoryItem.Scope.PROJECT}:
            raise ValidationError({"scope": "Для агента доступны только общая память и память проекта"})
        if scope == MemoryItem.Scope.PROJECT and not agent.project_id:
            raise ValidationError({"scope": "Агент не привязан к проекту"})
        payload = {
            "scope": scope,
            "project": str(agent.project_id) if scope == MemoryItem.Scope.PROJECT else None,
            "conversation": None,
            "memory_type": request.data.get("memory_type") or MemoryItem.Type.FACT,
            "content": str(request.data.get("content") or "").strip(),
            "importance_score": request.data.get("importance_score", "0.80"),
            "status": MemoryItem.Status.ACTIVE,
            "pinned": bool(request.data.get("pinned", True)),
            "enabled": True,
        }
        if not payload["content"]:
            raise ValidationError({"content": "Введите информацию, которую должен помнить агент"})
        serializer = MemoryItemSerializer(data=payload, context={"request": request})
        serializer.is_valid(raise_exception=True)
        item = serializer.save()
        return Response(MemoryItemSerializer(item, context={"request": request}).data, status=201)


class AgentMemoryItemView(APIView):
    def patch(self, request, agent_id, memory_id):
        agent = _agent(request, agent_id)
        item = _visible_item(agent, memory_id)
        allowed = {key: request.data[key] for key in ("content", "memory_type", "importance_score", "pinned", "enabled") if key in request.data}
        if not allowed:
            raise ValidationError({"detail": "Нет изменений"})
        serializer = MemoryItemSerializer(item, data=allowed, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        updated = serializer.save()
        return Response(MemoryItemSerializer(updated, context={"request": request}).data)

    def delete(self, request, agent_id, memory_id):
        agent = _agent(request, agent_id)
        item = _visible_item(agent, memory_id)
        item.status = MemoryItem.Status.DELETED
        item.enabled = False
        item.save(update_fields=["status", "enabled", "updated_at"])
        return Response(status=204)
