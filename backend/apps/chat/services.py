from .models import Conversation, Generation
from .streaming import prepare, run


def generate_reply(
    *,
    user,
    conversation: Conversation,
    content: str,
    client_message_id,
    idempotency_key: str,
    adapter=None,
):
    generation, created = prepare(
        user=user,
        conversation=conversation,
        content=content,
        client_message_id=client_message_id,
        idempotency_key=idempotency_key,
    )
    if created:
        list(run(generation, adapter=adapter))
        generation.refresh_from_db()
    return Generation.objects.select_related("assistant_message").get(pk=generation.pk)
