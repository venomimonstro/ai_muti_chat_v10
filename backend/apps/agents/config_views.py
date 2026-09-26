from copy import deepcopy

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Agent, AgentVersion
from .serializers import AgentSerializer, agent_has_active_run
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
    "condition",
    "wait",
    "notify",
    "finish",
    "github_read",
    "github_write",
    "code",
    "sandbox",
    "handoff",
}
PROMPT_NODE_TYPES = {"llm", "research", "web", "files", "image", "review", "analytics"}
CONDITION_OPERATORS = {"contains", "not_contains", "is_empty", "not_empty"}
CONDITION_SOURCES = {"previous_text", "objective"}


def _validate_graph(graph):
    if not isinstance(graph, dict):
        raise ValidationError({"graph": "Карта должна быть объектом"})
    nodes = deepcopy(graph.get("nodes") or [])
    edges = deepcopy(graph.get("edges") or [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValidationError({"graph": "nodes и edges должны быть массивами"})
    if not nodes:
        raise ValidationError({"graph": "Добавьте хотя бы один шаг"})
    if len(nodes) > 100:
        raise ValidationError({"graph": "Не более 100 блоков в карте агента"})

    ids = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ValidationError({"graph": f"Блок {index + 1} имеет неверный формат"})
        node_id = str(node.get("id") or "").strip()
        title = str(node.get("title") or "").strip()
        node_type = str(node.get("type") or "llm").strip().lower()
        if not node_id or len(node_id) > 120:
            raise ValidationError({"graph": f"У блока {index + 1} нет корректного id"})
        if not title or len(title) > 240:
            raise ValidationError({"graph": f"У блока {index + 1} нет корректного названия"})
        if node_type not in ALLOWED_NODE_TYPES:
            raise ValidationError({"graph": f"Тип блока {node_type} не поддерживается"})
        node["id"] = node_id
        node["title"] = title
        node["type"] = node_type

        if node_type in PROMPT_NODE_TYPES:
            prompt = str(node.get("prompt") or "").strip()
            if len(prompt) > 12000:
                raise ValidationError({"graph": f"Инструкция шага «{title}» слишком длинная"})
            node["prompt"] = prompt
        else:
            node.pop("prompt", None)

        if node_type == "publish":
            publish_status = str(node.get("status") or "draft").strip().lower()
            if publish_status not in {"draft", "publish"}:
                raise ValidationError({"graph": "Для публикации выберите draft или publish"})
            node["status"] = publish_status
        else:
            node.pop("status", None)

        if node_type == "condition":
            source = str(node.get("condition_source") or "previous_text").strip().lower()
            operator = str(node.get("operator") or "contains").strip().lower()
            if source not in CONDITION_SOURCES:
                raise ValidationError({"graph": f"У блока «{title}» неверный источник условия"})
            if operator not in CONDITION_OPERATORS:
                raise ValidationError({"graph": f"У блока «{title}» неверный оператор условия"})
            value = str(node.get("value") or "")
            if operator in {"contains", "not_contains"} and not value.strip():
                raise ValidationError({"graph": f"Укажите текст для проверки в условии «{title}»"})
            if len(value) > 2000:
                raise ValidationError({"graph": f"Текст условия «{title}» слишком длинный"})
            node["condition_source"] = source
            node["operator"] = operator
            node["value"] = value
        else:
            for key in ("condition_source", "operator", "value", "on_true", "on_false"):
                node.pop(key, None)

        if node_type == "wait":
            try:
                minutes = int(node.get("wait_minutes") or 60)
            except (TypeError, ValueError):
                raise ValidationError({"graph": f"У блока «{title}» неверное время ожидания"})
            if minutes < 1 or minutes > 10080:
                raise ValidationError({"graph": "Ожидание должно быть от 1 минуты до 7 дней"})
            node["wait_minutes"] = minutes
        else:
            node.pop("wait_minutes", None)

        if node_type == "notify":
            notification_title = str(node.get("notification_title") or "").strip()
            message = str(node.get("message") or "").strip()
            if len(notification_title) > 160:
                raise ValidationError({"graph": f"Заголовок уведомления «{title}» слишком длинный"})
            if len(message) > 4000:
                raise ValidationError({"graph": f"Текст уведомления «{title}» слишком длинный"})
            node["notification_title"] = notification_title
            node["message"] = message
        else:
            node.pop("notification_title", None)
            node.pop("message", None)

        ids.append(node_id)

    if len(ids) != len(set(ids)):
        raise ValidationError({"graph": "ID блоков карты должны быть уникальными"})
    known = set(ids)
    positions = {node_id: index for index, node_id in enumerate(ids)}

    seen_edges = set()
    outgoing_sources = set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValidationError({"graph": "Связь карты имеет неверный формат"})
        source = str(edge.get("from") or "").strip()
        target = str(edge.get("to") or "").strip()
        if source not in known or target not in known:
            raise ValidationError({"graph": "Связь ссылается на отсутствующий блок"})
        if positions[target] <= positions[source]:
            raise ValidationError({"graph": "Связи карты могут вести только вперёд. Циклические маршруты запрещены"})
        pair = (source, target)
        if pair in seen_edges:
            raise ValidationError({"graph": f"Связь {source} → {target} указана дважды"})
        if source in outgoing_sources:
            raise ValidationError(
                {"graph": f"У шага «{nodes[positions[source]]['title']}» может быть только один обычный переход. Для ветвления используйте блок «Условие»."}
            )
        seen_edges.add(pair)
        outgoing_sources.add(source)
        edge["from"] = source
        edge["to"] = target

    for index, node in enumerate(nodes):
        if node["type"] != "condition":
            continue
        for key, label in (("on_true", "ветка Да"), ("on_false", "ветка Нет")):
            target = str(node.get(key) or "").strip()
            if not target:
                node.pop(key, None)
                continue
            if target not in known:
                raise ValidationError({"graph": f"{label} условия «{node['title']}» ведёт к отсутствующему шагу"})
            if positions[target] <= index:
                raise ValidationError({"graph": f"{label} условия «{node['title']}» может вести только на более поздний шаг"})
            node[key] = target

    try:
        version = max(1, int(graph.get("version") or 1))
    except (TypeError, ValueError):
        raise ValidationError({"graph": "Некорректная версия карты"})
    return {"version": version, "nodes": nodes, "edges": edges}


def _ensure_agent_idle(agent):
    if agent_has_active_run(agent):
        raise ValidationError({"detail": "Нельзя менять конфигурацию агента во время активного запуска. Сначала завершите или остановите задачу."})


class AgentConfigView(APIView):
    @transaction.atomic
    def patch(self, request, agent_id):
        agent = get_object_or_404(Agent.objects.select_for_update(), id=agent_id, owner=request.user)
        _ensure_agent_idle(agent)
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
        _ensure_agent_idle(agent)
        version = get_object_or_404(AgentVersion, id=version_id, agent=agent)
        restore_agent_version(agent, version, request.user)
        return Response(AgentSerializer(agent, context={"request": request}).data)
