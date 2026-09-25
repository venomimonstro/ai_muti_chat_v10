from django.core.exceptions import ImproperlyConfigured, ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .branching import create_repository_branch
from .models import GitHubOperationLog
from .services import list_repository_directory
from .views import _binding_for


class GitHubRepositoryHealthView(APIView):
    def get(self, request, project_id):
        binding = _binding_for(request.user, project_id)
        read_ok = False
        error = ""
        item_count = 0
        try:
            tree = list_repository_directory(binding, "", ref=binding.default_branch)
            item_count = len(tree.get("items") or [])
            read_ok = True
            binding.last_synced_at = timezone.now()
            binding.save(update_fields=["last_synced_at", "updated_at"])
        except (DjangoValidationError, ImproperlyConfigured) as exc:
            error = str(exc)[:500]

        permissions = binding.installation.permissions or {}
        contents_permission = str(permissions.get("contents") or "").strip().lower()
        write_permission_known = contents_permission in {"write", "admin"}
        write_ready = bool(read_ok and binding.write_enabled and write_permission_known)
        return Response(
            {
                "healthy": read_ok,
                "read_ok": read_ok,
                "write_ready": write_ready,
                "write_enabled": bool(binding.write_enabled),
                "contents_permission": contents_permission or "unknown",
                "repository": binding.full_name,
                "default_branch": binding.default_branch,
                "root_items": item_count,
                "checked_at": timezone.now(),
                "error": error,
            }
        )


class GitHubWorkingBranchView(APIView):
    def post(self, request, project_id):
        binding = _binding_for(request.user, project_id)
        if request.data.get("confirm_create") is not True:
            raise ValidationError({"confirm_create": "Явное подтверждение создания ветки обязательно"})
        branch = str(request.data.get("branch") or "").strip()
        from_ref = str(request.data.get("from_ref") or binding.default_branch).strip()
        try:
            result = create_repository_branch(binding, branch, from_ref=from_ref)
        except DjangoValidationError as exc:
            GitHubOperationLog.objects.create(
                actor=request.user,
                binding=binding,
                action="create_branch",
                branch=branch[:255],
                success=False,
                metadata={"error": str(exc)[:300], "from_ref": from_ref[:255]},
            )
            raise ValidationError(str(exc)) from exc
        GitHubOperationLog.objects.create(
            actor=request.user,
            binding=binding,
            action="create_branch",
            branch=result["branch"],
            success=True,
            metadata={"from_ref": result["base_branch"], "sha": result["sha"]},
        )
        return Response(result)
