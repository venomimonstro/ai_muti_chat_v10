from django.contrib import admin

from .models import Conversation, Generation, GenerationAttempt, Message

admin.site.register(Conversation)
admin.site.register(Message)
admin.site.register(Generation)
admin.site.register(GenerationAttempt)
