from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .branching import create_repository_branch
from .models import GitHubOperationLog
from .views import _binding_for


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
