"""Eliminate stale Conversation objects between preview and transactional prepare."""

from .models import Conversation


def install(streaming_module) -> None:
    raw_prepare = streaming_module.prepare
    if getattr(raw_prepare, "_ai_workspace_fresh_conversation", False):
        return

    def prepare(*, user, conversation, **kwargs):
        # The HTTP view can spend time on preview/confirmation before prepare starts,
        # while another tab may change routing mode, model, project or memory. Start
        # the durable request from a freshly loaded object; raw prepare then performs
        # its own select_for_update and remains authoritative for the actual route.
        fresh = (
            Conversation.objects.select_related("project")
            .filter(pk=conversation.pk, owner=user)
            .first()
        )
        if fresh is None:
            return raw_prepare(user=user, conversation=conversation, **kwargs)
        return raw_prepare(user=user, conversation=fresh, **kwargs)

    prepare._ai_workspace_fresh_conversation = True
    prepare._raw_prepare = raw_prepare
    streaming_module.prepare = prepare
