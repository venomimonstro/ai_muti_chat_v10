from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Conversation
from .serializers import ConversationSerializer, SendMessageSerializer
from .services import generate_reply


class ConversationViewSet(viewsets.ModelViewSet):
    serializer_class = ConversationSerializer

    def get_queryset(self):
        return Conversation.objects.filter(owner=self.request.user).prefetch_related("messages")

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=["post"])
    def messages(self, request, pk=None):
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = request.headers.get("Idempotency-Key")
        if not key:
            return Response(
                {"detail": "Idempotency-Key обязателен"}, status=status.HTTP_400_BAD_REQUEST
            )
        generation = generate_reply(
            user=request.user,
            conversation=self.get_object(),
            idempotency_key=key,
            **serializer.validated_data,
        )
        return Response(
            {
                "generation_id": generation.id,
                "state": generation.state,
                "message": ConversationSerializer(self.get_object()).data["messages"][-1],
            },
            status=status.HTTP_200_OK,
        )
