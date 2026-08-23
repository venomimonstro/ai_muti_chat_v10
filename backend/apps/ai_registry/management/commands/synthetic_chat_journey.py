import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import PriceVersion
from apps.billing.services import credit
from apps.chat.models import Conversation, Generation
from apps.chat.streaming import prepare, run


class Command(BaseCommand):
    help = "Runs a rollback-only synthetic chat, billing and streaming journey."

    def handle(self, *_args, **_options):
        passed = False
        with transaction.atomic():
            suffix = uuid.uuid4().hex[:10]
            provider = Provider.objects.create(
                slug=f"synthetic-{suffix}",
                name="Synthetic Echo",
                adapter_type=Provider.AdapterType.ECHO,
            )
            model = AIModel.objects.create(
                provider=provider,
                slug=f"synthetic-echo-{suffix}",
                display_name="Synthetic Echo",
                upstream_model="echo-v1",
                capabilities=["text", "streaming"],
            )
            PriceVersion.objects.create(
                model_slug=model.slug,
                input_rub_per_million=Decimal("10"),
                output_rub_per_million=Decimal("20"),
                markup_percent=Decimal("100"),
                effective_from=timezone.now(),
            )
            user = get_user_model().objects.create_user(
                username=f"synthetic-{suffix}",
                email=f"synthetic-{suffix}@invalid.local",
                password=None,
            )
            credit(user, Decimal("10"), "synthetic", suffix, bucket="promo")
            conversation = Conversation.objects.create(owner=user, selected_model=model.slug)
            generation, _ = prepare(
                user=user,
                conversation=conversation,
                content="synthetic health check",
                client_message_id=uuid.uuid4(),
                idempotency_key=f"synthetic:{suffix}",
            )
            list(run(generation))
            generation.refresh_from_db()
            passed = generation.state == Generation.State.COMPLETED
            transaction.set_rollback(True)
        if not passed:
            raise CommandError("Synthetic journey failed")
        self.stdout.write(self.style.SUCCESS("Synthetic chat journey passed"))
