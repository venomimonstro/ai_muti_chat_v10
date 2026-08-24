from django.db.models import Q
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chat.models import Conversation, Message
from apps.files.models import FileAsset
from apps.projects.access import accessible_projects


def excerpt(value, query, radius=90):
    normalized = value.replace("\n", " ").strip()
    position = normalized.casefold().find(query.casefold())
    if position < 0:
        return normalized[: radius * 2]
    start = max(position - radius, 0)
    end = min(position + len(query) + radius, len(normalized))
    prefix = "…" if start else ""
    suffix = "…" if end < len(normalized) else ""
    return f"{prefix}{normalized[start:end]}{suffix}"


class WorkspaceSearchView(APIView):
    def get(self, request):
        query = request.query_params.get("q", "").strip()
        if len(query) < 2 or len(query) > 200:
            raise ValidationError({"q": "Введите от 2 до 200 символов"})
        projects = accessible_projects(request.user)
        conversations = Conversation.objects.filter(owner=request.user)
        results = []
        for conversation in conversations.filter(title__icontains=query)[:8]:
            results.append(
                {
                    "type": "conversation",
                    "id": conversation.id,
                    "title": conversation.title,
                    "excerpt": conversation.title,
                }
            )
        messages = (
            Message.objects.filter(conversation__owner=request.user, content__icontains=query)
            .select_related("conversation")
            .order_by("-created_at")[:8]
        )
        for message in messages:
            results.append(
                {
                    "type": "message",
                    "id": message.id,
                    "conversation_id": message.conversation_id,
                    "title": message.conversation.title,
                    "excerpt": excerpt(message.content, query),
                }
            )
        for project in projects.filter(Q(name__icontains=query) | Q(description__icontains=query))[
            :8
        ]:
            results.append(
                {
                    "type": "project",
                    "id": project.id,
                    "title": project.name,
                    "excerpt": excerpt(project.description or project.name, query),
                }
            )
        files = (
            FileAsset.objects.filter(
                Q(owner=request.user) | Q(project__in=projects),
                original_name__icontains=query,
                deleted_at__isnull=True,
            )
            .distinct()
            .order_by("-created_at")[:8]
        )
        for asset in files:
            results.append(
                {
                    "type": "file",
                    "id": asset.id,
                    "project_id": asset.project_id,
                    "title": asset.original_name,
                    "excerpt": asset.get_status_display(),
                }
            )
        return Response({"query": query, "results": results[:24]})
