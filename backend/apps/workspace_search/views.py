from uuid import UUID

from django.utils.dateparse import parse_date
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chat.models import Conversation
from apps.projects.access import accessible_projects

from .search import VALID_ROLES, VALID_TYPES, SearchFilters, search_workspace


class WorkspaceSearchView(APIView):
    def get(self, request):
        query = request.query_params.get("q", "").strip()
        if len(query) < 2 or len(query) > 200:
            raise ValidationError({"q": "Введите от 2 до 200 символов"})
        requested_types = {
            item.strip() for item in request.query_params.get("type", "").split(",") if item.strip()
        }
        types = requested_types or VALID_TYPES
        if not types <= VALID_TYPES:
            raise ValidationError({"type": "Неизвестный тип результата"})
        role = request.query_params.get("role") or None
        if role and role not in VALID_ROLES:
            raise ValidationError({"role": "Неизвестная роль сообщения"})
        project_id = request.query_params.get("project") or None
        try:
            if project_id:
                UUID(project_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"project": "Некорректный идентификатор проекта"}) from exc
        if project_id and not accessible_projects(request.user).filter(pk=project_id).exists():
            raise ValidationError({"project": "Проект не найден или недоступен"})
        conversation_id = request.query_params.get("conversation") or None
        try:
            if conversation_id:
                UUID(conversation_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"conversation": "Некорректный идентификатор чата"}) from exc
        if (
            conversation_id
            and not Conversation.objects.filter(pk=conversation_id, owner=request.user).exists()
        ):
            raise ValidationError({"conversation": "Чат не найден или недоступен"})
        date_from = parse_date(request.query_params.get("date_from", ""))
        date_to = parse_date(request.query_params.get("date_to", ""))
        if request.query_params.get("date_from") and date_from is None:
            raise ValidationError({"date_from": "Используйте формат YYYY-MM-DD"})
        if request.query_params.get("date_to") and date_to is None:
            raise ValidationError({"date_to": "Используйте формат YYYY-MM-DD"})
        if date_from and date_to and date_from > date_to:
            raise ValidationError({"date_to": "Дата окончания раньше даты начала"})
        try:
            limit = max(1, min(int(request.query_params.get("limit", 24)), 50))
        except ValueError as exc:
            raise ValidationError({"limit": "Ожидается целое число"}) from exc
        filters = SearchFilters(
            types=frozenset(types),
            project_id=project_id,
            conversation_id=conversation_id,
            role=role,
            date_from=date_from,
            date_to=date_to,
        )
        return Response(
            {
                "query": query,
                "filters": {
                    "type": sorted(types),
                    "project": project_id,
                    "conversation": conversation_id,
                    "role": role,
                    "date_from": date_from,
                    "date_to": date_to,
                },
                "results": search_workspace(
                    user=request.user, query=query, filters=filters, limit=limit
                ),
            }
        )
