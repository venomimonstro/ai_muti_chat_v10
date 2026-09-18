from rest_framework.response import Response

from .views import BackupActionView


class SafeBackupActionView(BackupActionView):
    """Manual admin actions cannot manufacture disaster-recovery evidence."""

    def post(self, request, backup_id):
        if request.data.get("action") == "restore_drill":
            return Response(
                {
                    "detail": (
                        "Тест восстановления нельзя отметить вручную. "
                        "Выполните scripts/restore_drill.sh: он восстановит копию "
                        "в изолированную БД, проверит миграции и финансовые инварианты, "
                        "после чего сам запишет доказательство."
                    )
                },
                status=409,
            )
        return super().post(request, backup_id)
