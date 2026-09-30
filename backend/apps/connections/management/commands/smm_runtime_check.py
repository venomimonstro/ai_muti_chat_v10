import os

from django.core.management.base import BaseCommand

from apps.connections.models import ExternalConnection
from apps.connections.smm_models import SMMContentItem, SMMContentPlan


class Command(BaseCommand):
    help = "Проверяет production-готовность VK/SMM Studio без публикации внешних постов."

    def handle(self, *args, **options):
        vk_app = bool(os.getenv("VK_APP_ID", "").strip() and os.getenv("VK_APP_SECRET", "").strip())
        pexels = bool(os.getenv("PEXELS_API_KEY", "").strip())
        vk_connections = ExternalConnection.objects.filter(kind=ExternalConnection.Kind.VK)
        healthy = vk_connections.filter(enabled=True, health_state=ExternalConnection.Health.HEALTHY)
        selected = [
            connection
            for connection in healthy.only("id", "name", "metadata")
            if str((connection.metadata or {}).get("selected_group_id") or "").strip()
        ]
        plans = SMMContentPlan.objects.count()
        scheduled = SMMContentItem.objects.filter(status=SMMContentItem.Status.SCHEDULED).count()
        due = SMMContentItem.objects.filter(
            status=SMMContentItem.Status.SCHEDULED,
            plan__auto_publish=True,
        ).count()

        self.stdout.write(f"VK_OAUTH_CONFIGURED={'yes' if vk_app else 'no'}")
        self.stdout.write(f"PEXELS_CONFIGURED={'yes' if pexels else 'no'}")
        self.stdout.write(f"VK_CONNECTIONS={vk_connections.count()}")
        self.stdout.write(f"VK_HEALTHY={healthy.count()}")
        self.stdout.write(f"VK_SELECTED_GROUPS={len(selected)}")
        self.stdout.write(f"SMM_PLANS={plans}")
        self.stdout.write(f"SMM_SCHEDULED={scheduled}")
        self.stdout.write(f"SMM_AUTOPUBLISH_ITEMS={due}")
        for connection in selected[:20]:
            metadata = connection.metadata or {}
            self.stdout.write(
                "VK_READY "
                f"connection={connection.id} "
                f"name={connection.name!r} "
                f"group={str(metadata.get('selected_group_name') or metadata.get('selected_group_id') or '')!r}"
            )

        if not vk_app:
            self.stdout.write(self.style.WARNING("SMM_BLOCKER: VK_APP_ID/VK_APP_SECRET не настроены"))
        elif not selected:
            self.stdout.write(self.style.WARNING("SMM_WAITING: нет healthy VK-подключения с выбранным сообществом"))
        else:
            self.stdout.write(self.style.SUCCESS("SMM_RUNTIME_READY: OAuth и рабочее VK-сообщество настроены"))
        if not pexels:
            self.stdout.write(self.style.WARNING("SMM_OPTIONAL: PEXELS_API_KEY не задан, фотосток будет недоступен"))
