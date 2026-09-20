from django.db import transaction
from django.test import RequestFactory, TestCase

from apps.accounts.models import User

from .message_actions import OwnedConversationAction
from .models import Conversation
from .ux_models import ConversationUIState


class ChatActionLockingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="lock-action-user",
            email="lock-action@example.test",
            password="test-password-123",
        )
        self.conversation = Conversation.objects.create(owner=self.user, title="Lock test")
        self.request = RequestFactory().post("/")
        self.request.user = self.user

    def test_locked_lookup_does_not_join_nullable_ui_state(self):
        with transaction.atomic():
            found = OwnedConversationAction().conversation(
                self.request,
                self.conversation.id,
                lock=True,
            )
        self.assertIsNotNone(found)
        self.assertEqual(found.id, self.conversation.id)

    def test_locked_lookup_rejects_soft_deleted_chat(self):
        ConversationUIState.objects.create(
            conversation=self.conversation,
            owner=self.user,
            deleted_at=self.conversation.updated_at,
        )
        with transaction.atomic():
            found = OwnedConversationAction().conversation(
                self.request,
                self.conversation.id,
                lock=True,
            )
        self.assertIsNone(found)
