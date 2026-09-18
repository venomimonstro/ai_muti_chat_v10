from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Conversation, Message


@receiver(post_save, sender=Message)
def touch_conversation_on_user_message(sender, instance, created, **kwargs):
    if created and instance.role == Message.Role.USER:
        Conversation.objects.filter(pk=instance.conversation_id).update(updated_at=timezone.now())
