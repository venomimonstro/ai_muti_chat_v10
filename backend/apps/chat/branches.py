from django.db import transaction

from .models import ConversationBranch, Message


@transaction.atomic
def ensure_active_branch(conversation, user=None):
    if conversation.active_branch_id:
        return conversation.active_branch
    branch = ConversationBranch.objects.create(
        conversation=conversation,
        title="Основная ветка",
        created_by=user or conversation.owner,
    )
    Message.objects.filter(conversation=conversation, branch__isnull=True).update(branch=branch)
    conversation.active_branch = branch
    conversation.save(update_fields=["active_branch", "updated_at"])
    return branch


def visible_messages(conversation):
    if not conversation.active_branch_id:
        return conversation.messages.all()
    branch = conversation.active_branch
    inherited = branch.inherited_message_ids
    return conversation.messages.filter(branch=branch) | conversation.messages.filter(
        id__in=inherited
    )


@transaction.atomic
def fork_branch(*, conversation, user, source_message, title="Альтернативная ветка"):
    active = ensure_active_branch(conversation, user)
    visible = list(visible_messages(conversation).order_by("created_at"))
    inherited = []
    found = False
    for message in visible:
        inherited.append(str(message.id))
        if message.id == source_message.id:
            found = True
            break
    if not found:
        raise ValueError("Сообщение не входит в активную ветку")
    branch = ConversationBranch.objects.create(
        conversation=conversation,
        parent=active,
        forked_from=source_message,
        title=title[:160],
        inherited_message_ids=inherited,
        created_by=user,
    )
    conversation.active_branch = branch
    conversation.save(update_fields=["active_branch", "updated_at"])
    return branch
