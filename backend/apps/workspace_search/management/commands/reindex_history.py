from django.core.management.base import BaseCommand

from apps.chat.models import Message
from apps.workspace_search.embeddings import index_message


class Command(BaseCommand):
    help = "Rebuild semantic search embeddings for chat history"

    def add_arguments(self, parser):
        parser.add_argument("--user", dest="user_id")
        parser.add_argument("--conversation", dest="conversation_id")

    def handle(self, *_args, **options):
        messages = Message.objects.exclude(content="").order_by("created_at")
        if options["user_id"]:
            messages = messages.filter(conversation__owner_id=options["user_id"])
        if options["conversation_id"]:
            messages = messages.filter(conversation_id=options["conversation_id"])
        updated = 0
        for message in messages.iterator():
            index_message(message)
            updated += 1
        self.stdout.write(self.style.SUCCESS(f"Переиндексировано сообщений: {updated}"))
