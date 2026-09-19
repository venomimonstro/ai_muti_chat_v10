from django.core.exceptions import ImproperlyConfigured, ValidationError as DjangoValidationError
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.projects.models import Project

from .models import GitHubOperationLog, GitHubRepositoryBinding
from .mutations import create_repository_file, delete_repository_file


def _binding_for(user, project_id):
    project = Project.objects.filter(pk=project_id, owner=user, archived_at__isnull=True).first()
    if project is None:
        raise NotFound("Проект не найден")
    try:
        return project.github_repository
    except GitHubRepositoryBinding.DoesNotExist as exc:
        raise NotFound("GitHub repository не подключён к проекту") from exc


class GitHubFileCreateView(APIView):
    def post(self, request, project_id):
        binding = _binding_for(request.user, project_id)
        if request.data.get("confirm_write") is not True:
            raise ValidationError({"confirm_write": "Явное подтверждение создания файла обязательно"})
        path = str(request.data.get("path") or "")[:1024]
        branch = str(request.data.get("branch") or binding.default_branch)[:255]
        try:
            result = create_repository_file(
                binding,
                path,
                content=request.data.get("content", ""),
                message=request.data.get("message"),
                branch=request.data.get("branch") or None,
            )
        except (DjangoValidationError, ImproperlyConfigured) as exc:
            GitHubOperationLog.objects.create(
                actor=request.user,
                binding=binding,
                action="create_file",
                path=path,
                branch=branch,
                success=False,
                metadata={"error": str(exc)[:300]},
            )
            raise ValidationError(str(exc)) from exc
        GitHubOperationLog.objects.create(
            actor=request.user,
            binding=binding,
            action="create_file",
            path=result["path"],
            branch=result["branch"],
            success=True,
            metadata={"commit_sha": result["commit_sha"], "content_sha": result["content_sha"]},
        )
        return Response(result, status=201)


class GitHubFileDeleteView(APIView):
    def post(self, request, project_id):
        binding = _binding_for(request.user, project_id)
        if request.data.get("confirm_delete") is not True:
            raise ValidationError({"confirm_delete": "Явное подтверждение удаления файла обязательно"})
        path = str(request.data.get("path") or "")[:1024]
        branch = str(request.data.get("branch") or binding.default_branch)[:255]
        try:
            result = delete_repository_file(
                binding,
                path,
                expected_sha=request.data.get("expected_sha"),
                message=request.data.get("message"),
                branch=request.data.get("branch") or None,
            )
        except (DjangoValidationError, ImproperlyConfigured) as exc:
            GitHubOperationLog.objects.create(
                actor=request.user,
                binding=binding,
                action="delete_file",
                path=path,
                branch=branch,
                success=False,
                metadata={"error": str(exc)[:300]},
            )
            raise ValidationError(str(exc)) from exc
        GitHubOperationLog.objects.create(
            actor=request.user,
            binding=binding,
            action="delete_file",
            path=result["path"],
            branch=result["branch"],
            success=True,
            metadata={"commit_sha": result["commit_sha"]},
        )
        return Response(result)
