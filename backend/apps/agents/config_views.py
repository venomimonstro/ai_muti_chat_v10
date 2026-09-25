from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Agent, AgentVersion
from .serializers import AgentSerializer
from .versioning import create_agent_version, restore_agent_version


ALLOWED_NODE_TYPES = {
    "llm",
    "research",
    "web",
    "files",
    "image",
    "review",
    "approval",
    "publish",
    "analytics",
    "github_read",
    "github_write",
    "code",
    "sandbox",
    "handoff",
    "wait",
    "finish",
}


def _validate_graph(graph):
    if not isinstance(graph, dict):
        raise ValidationError({"graph": "Карта должна быть объектом"})
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValidationError({"graph": "nodes и edges должны быть массивами"})
    if len(nodes) > 100:
        raise ValidationError({"graph": "Не более 100 блоков в карте агента"})
    ids = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ValidationError({"graph": f"Блок {index + 1} имеет неверный формат"})
        node_id = str(node.get("id") or "").strip()
        title = str(node.get("title") or "").strip()
        node_type = str(node.get("type") or "llm").strip()
        if not node_id or len(node_id) > 120:
            raise ValidationError({"graph": f"У блока {index + 1} нет корректного id"})
        if not title or len(title) > 240:
            raise ValidationError({"graph": f"У блока {index + 1} нет корректного названия"})
        if node_type not in ALLOWED_NODE_TYPES:
            raise ValidationError({"graph": f"Тип блока {node_type} не поддерживается"})
        ids.append(node_id)
    if len(ids) != len(set(ids)):
        raise ValidationError({"graph": "ID блоков карты должны быть уникальными"})
    known = set(ids)
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValidationError({"graph": "Связь карты имеет неверный формат"})
        source = str(edge.get("from") or "").strip()
        target = str(edge.get("to") or "").strip()
        if source not in known or target not in known:
            raise ValidationError({"graph": "Связь ссылается на отсутствующий блок"})
        if source == target:
            raise ValidationError({"graph": "Блок нельзя связать с самим собой"})
    return {"version": int(graph.get("version") or 1), "nodes": nodes, "edges": edges}


class AgentConfigView(APIView):
    @transaction.atomic
    def patch(self, request, agent_id):
        agent = get_object_or_404(Agent.objects.select_for_update(), id=agent_id, owner=request.user)
        payload = dict(request.data)
        if "graph" in payload:
            payload["graph"] = _validate_graph(payload["graph"])
        serializer = AgentSerializer(agent, data=payload, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        create_agent_version(agent, request.user)
        serializer.save()
        return Response(serializer.data)


class AgentVersionListView(APIView):
    def get(self, request, agent_id):
        agent = get_object_or_404(Agent, id=agent_id, owner=request.user)
        rows = AgentVersion.objects.filter(agent=agent).select_related("created_by")[:50]
        return Response(
            [
                {
                    "id": str(item.id),
                    "version": item.version,
                    "created_at": item.created_at,
                    "created_by": item.created_by.get_username(),
                    "summary": {
                        "name": (item.snapshot or {}).get("name"),
                        "role": (item.snapshot or {}).get("role"),
                        "autonomy": (item.snapshot or {}).get("autonomy"),
                        "system_level": (item.snapshot or {}).get("system_level"),
                    },
                }
                for item in rows
            ]
        )


class AgentVersionRestoreView(APIView):
    @transaction.atomic
    def post(self, request, agent_id, version_id):
        agent = get_object_or_404(Agent.objects.select_for_update(), id=agent_id, owner=request.user)
        version = get_object_or_404(AgentVersion, id=version_id, agent=agent)
        restore_agent_version(agent, version, request.user)
        return Response(AgentSerializer(agent, context={"request": request}).data)
