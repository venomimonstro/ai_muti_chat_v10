from apps.chat.ux_models import ConversationUIState

from .views import WorkspaceSearchView


class SafeWorkspaceSearchView(WorkspaceSearchView):
    def get(self, request):
        response = super().get(request)
        deleted_ids = {
            str(value)
            for value in ConversationUIState.objects.filter(
                owner=request.user,
                deleted_at__isnull=False,
            ).values_list("conversation_id", flat=True)
        }
        if deleted_ids and isinstance(response.data, dict):
            response.data["results"] = [
                item
                for item in response.data.get("results", [])
                if str(item.get("conversation_id") or "") not in deleted_ids
            ]
        return response
