from django.core import signing
from django.core.exceptions import ImproperlyConfigured, ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.projects.access import accessible_projects

from .models import GitHubInstallation, GitHubOperationLog, GitHubRepositoryBinding
from .services import (
    configured,
    exchange_user_code,
    install_url,
    list_repositories,
    list_repository_directory,
    read_repository_file,
    verified_installation,
    write_repository_file,
)

STATE_SALT = "github.integration.install.v1"
STATE_MAX_AGE = 15 * 60


def _binding_for(user, project_id, *, write=False):
    project = accessible_projects(user, write=write).filter(pk=project_id).first()
    if project is None:
        raise NotFound("Проект не найден")
    try:
        return project.github_repository
    except GitHubRepositoryBinding.DoesNotExist as exc:
        raise NotFound("GitHub repository не подключён к проекту") from exc


class GitHubConnectView(APIView):
    def get(self, request):
        if not configured():
            return Response({"configured": False, "connect_url": None})
        state = signing.dumps({"user_id": str(request.user.id)}, salt=STATE_SALT, compress=True)
        return Response({"configured": True, "connect_url": install_url(state)})


class GitHubCallbackView(APIView):
    def get(self, request):
        code = str(request.query_params.get("code") or "").strip()
        state = str(request.query_params.get("state") or "").strip()
        installation_id = request.query_params.get("installation_id")
        if not code or not state or not installation_id:
            raise ValidationError("GitHub callback is incomplete")
        try:
            payload = signing.loads(state, salt=STATE_SALT, max_age=STATE_MAX_AGE)
        except signing.BadSignature as exc:
            raise ValidationError("GitHub state is invalid or expired") from exc
        if payload.get("user_id") != str(request.user.id):
            raise ValidationError("GitHub state belongs to another user")
        try:
            normalized_installation_id = int(installation_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError("GitHub installation id is invalid") from exc
        existing = GitHubInstallation.objects.filter(installation_id=normalized_installation_id).first()
        if existing is not None and existing.owner_id != request.user.id:
            raise ValidationError("Эта GitHub installation уже привязана к другому аккаунту сервиса")
        try:
            user_token = exchange_user_code(code)
            installation = verified_installation(user_token, normalized_installation_id)
        except (DjangoValidationError, ImproperlyConfigured) as exc:
            raise ValidationError(str(exc)) from exc
        account = installation.get("account") or {}
        if existing is None:
            record = GitHubInstallation.objects.create(
                installation_id=normalized_installation_id,
                owner=request.user,
                account_login=str(account.get("login") or "")[:255],
                account_type=str(account.get("type") or "")[:40],
                repository_selection=str(installation.get("repository_selection") or "")[:24],
                permissions=installation.get("permissions") or {},
                active=True,
            )
        else:
            record = existing
            record.account_login = str(account.get("login") or "")[:255]
            record.account_type = str(account.get("type") or "")[:40]
            record.repository_selection = str(installation.get("repository_selection") or "")[:24]
            record.permissions = installation.get("permissions") or {}
            record.active = True
            record.save(update_fields=[
                "account_login", "account_type", "repository_selection", "permissions", "active", "updated_at"
            ])
        return Response({
            "connected": True,
            "installation": str(record.id),
            "account": record.account_login,
            "repository_selection": record.repository_selection,
        })


class GitHubInstallationListView(APIView):
    def get(self, request):
        items = []
        for installation in GitHubInstallation.objects.filter(owner=request.user, active=True):
            items.append({
                "id": str(installation.id),
                "installation_id": installation.installation_id,
                "account": installation.account_login,
                "repository_selection": installation.repository_selection,
            })
        return Response(items)


class GitHubRepositoryListView(APIView):
    def get(self, request, installation_id):
        installation = GitHubInstallation.objects.filter(
            pk=installation_id, owner=request.user, active=True
        ).first()
        if installation is None:
            raise NotFound("GitHub installation не найдена")
        try:
            repositories = list_repositories(installation.installation_id)
        except (DjangoValidationError, ImproperlyConfigured) as exc:
            raise ValidationError(str(exc)) from exc
        return Response([
            {
                "id": item.get("id"),
                "full_name": item.get("full_name"),
                "private": bool(item.get("private")),
                "default_branch": item.get("default_branch") or "main",
            }
            for item in repositories
        ])


class GitHubProjectBindingView(APIView):
    def get(self, request, project_id):
        binding = _binding_for(request.user, project_id)
        return Response({
            "id": str(binding.id),
            "repository_id": binding.repository_id,
            "full_name": binding.full_name,
            "default_branch": binding.default_branch,
            "private": binding.private,
            "write_enabled": binding.write_enabled,
            "last_synced_at": binding.last_synced_at,
        })

    def post(self, request, project_id):
        project = accessible_projects(request.user, write=True).filter(pk=project_id).first()
        if project is None:
            raise NotFound("Проект не найден")
        installation = GitHubInstallation.objects.filter(
            pk=request.data.get("installation"), owner=request.user, active=True
        ).first()
        if installation is None:
            raise ValidationError({"installation": "GitHub installation не найдена"})
        try:
            repositories = list_repositories(installation.installation_id)
        except (DjangoValidationError, ImproperlyConfigured) as exc:
            raise ValidationError(str(exc)) from exc
        try:
            requested_id = int(request.data.get("repository_id"))
        except (TypeError, ValueError) as exc:
            raise ValidationError({"repository_id": "Некорректный repository_id"}) from exc
        repository = next((item for item in repositories if int(item.get("id", 0)) == requested_id), None)
        if repository is None:
            raise ValidationError({"repository_id": "Репозиторий не доступен этой установке"})
        binding, _ = GitHubRepositoryBinding.objects.update_or_create(
            project=project,
            defaults={
                "installation": installation,
                "repository_id": requested_id,
                "full_name": str(repository.get("full_name") or "")[:255],
                "default_branch": str(repository.get("default_branch") or "main")[:255],
                "private": bool(repository.get("private")),
                "write_enabled": False,
                "last_synced_at": timezone.now(),
            },
        )
        GitHubOperationLog.objects.create(
            actor=request.user,
            binding=binding,
            action="bind_repository",
            success=True,
            metadata={"repository_id": requested_id},
        )
        return Response({
            "id": str(binding.id),
            "full_name": binding.full_name,
            "default_branch": binding.default_branch,
            "write_enabled": binding.write_enabled,
        })

    def patch(self, request, project_id):
        binding = _binding_for(request.user, project_id, write=True)
        if "write_enabled" not in request.data:
            raise ValidationError({"write_enabled": "Поле обязательно"})
        binding.write_enabled = request.data.get("write_enabled") is True
        binding.save(update_fields=["write_enabled", "updated_at"])
        GitHubOperationLog.objects.create(
            actor=request.user,
            binding=binding,
            action="write_access_changed",
            success=True,
            metadata={"write_enabled": binding.write_enabled},
        )
        return Response({"write_enabled": binding.write_enabled})


class GitHubDirectoryView(APIView):
    def get(self, request, project_id):
        binding = _binding_for(request.user, project_id)
        path = request.query_params.get("path", "")
        ref = request.query_params.get("ref") or None
        try:
            payload = list_repository_directory(binding, path, ref=ref)
        except (DjangoValidationError, ImproperlyConfigured) as exc:
            raise ValidationError(str(exc)) from exc
        GitHubOperationLog.objects.create(
            actor=request.user,
            binding=binding,
            action="list_directory",
            path=payload["path"],
            branch=payload["ref"],
            success=True,
            metadata={"items": len(payload["items"])},
        )
        return Response(payload)


class GitHubFileView(APIView):
    def get(self, request, project_id):
        binding = _binding_for(request.user, project_id)
        path = request.query_params.get("path")
        ref = request.query_params.get("ref") or None
        try:
            payload = read_repository_file(binding, path, ref=ref)
        except (DjangoValidationError, ImproperlyConfigured) as exc:
            raise ValidationError(str(exc)) from exc
        GitHubOperationLog.objects.create(
            actor=request.user, binding=binding, action="read_file", path=payload["path"],
            branch=payload["ref"], success=True,
        )
        return Response(payload)

    def put(self, request, project_id):
        binding = _binding_for(request.user, project_id, write=True)
        if request.data.get("confirm_write") is not True:
            raise ValidationError({"confirm_write": "Явное подтверждение записи обязательно"})
        try:
            result = write_repository_file(
                binding,
                request.data.get("path"),
                content=request.data.get("content", ""),
                expected_sha=str(request.data.get("expected_sha") or ""),
                message=request.data.get("message"),
                branch=request.data.get("branch") or None,
            )
        except (DjangoValidationError, ImproperlyConfigured) as exc:
            GitHubOperationLog.objects.create(
                actor=request.user,
                binding=binding,
                action="write_file",
                path=str(request.data.get("path") or "")[:1024],
                branch=str(request.data.get("branch") or binding.default_branch)[:255],
                success=False,
                metadata={"error": str(exc)[:300]},
            )
            raise ValidationError(str(exc)) from exc
        GitHubOperationLog.objects.create(
            actor=request.user,
            binding=binding,
            action="write_file",
            path=result["path"],
            branch=result["branch"],
            success=True,
            metadata={"commit_sha": result["commit_sha"], "content_sha": result["content_sha"]},
        )
        return Response(result)