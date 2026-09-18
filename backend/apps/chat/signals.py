import re

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Conversation, Message


def prompt_title(content: str, max_length: int = 72) -> str:
    value = re.sub(r"\s+", " ", content).strip()
    if not value:
        return "Новый чат"
    if len(value) <= max_length:
        return value
    shortened = value[: max_length + 1].rsplit(" ", 1)[0].strip()
    if len(shortened) < 20:
        shortened = value[:max_length].strip()
    return f"{shortened.rstrip('.,;:!?')}…"


@receiver(post_save, sender=Message)
def touch_conversation_on_user_message(sender, instance, created, **kwargs):
    if not created or instance.role != Message.Role.USER:
        return
    updates = {"updated_at": timezone.now()}
    conversation = Conversation.objects.filter(pk=instance.conversation_id).only("title").first()
    if conversation and conversation.title == "Новый чат":
        has_earlier_user_message = Message.objects.filter(
            conversation_id=instance.conversation_id,
            role=Message.Role.USER,
        ).exclude(pk=instance.pk).exists()
        if not has_earlier_user_message:
            updates["title"] = prompt_title(instance.content)
    Conversation.objects.filter(pk=instance.conversation_id).update(**updates)
