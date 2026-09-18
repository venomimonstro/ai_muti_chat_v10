import pytest

from apps.accounts.models import User
from apps.chat.models import Conversation, Message
from apps.chat.signals import prompt_title


def test_prompt_title_is_compact_and_readable():
    assert prompt_title("  Короткий   вопрос\nс пробелами ") == "Короткий вопрос с пробелами"
    title = prompt_title("слово " * 40)
    assert title.endswith("…")
    assert len(title) <= 73


@pytest.mark.django_db
def test_first_user_message_titles_new_chat_only_once():
    user = User.objects.create_user(
        username="title-user",
        email="title@example.test",
        password="test-password-123",
    )
    conversation = Conversation.objects.create(owner=user, title="Новый чат")
    Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Составь план продвижения интернет-магазина",
    )
    conversation.refresh_from_db()
    assert conversation.title == "Составь план продвижения интернет-магазина"

    Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Второй вопрос не должен менять название",
    )
    conversation.refresh_from_db()
    assert conversation.title == "Составь план продвижения интернет-магазина"


@pytest.mark.django_db
def test_custom_title_is_never_overwritten():
    user = User.objects.create_user(
        username="custom-title-user",
        email="custom-title@example.test",
        password="test-password-123",
    )
    conversation = Conversation.objects.create(owner=user, title="Моя папка идей")
    Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Первый запрос",
    )
    conversation.refresh_from_db()
    assert conversation.title == "Моя папка идей"
