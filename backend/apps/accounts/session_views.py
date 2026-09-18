from django.contrib.sessions.models import Session
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from rest_framework.response import Response
from rest_framework.views import APIView

SESSION_PUBLIC_ID_SALT = "accounts.session.public-id.v1"


def session_public_id(session_key: str) -> str:
    """Return a stable opaque identifier without exposing the bearer session secret."""
    return salted_hmac(SESSION_PUBLIC_ID_SALT, session_key).hexdigest()


def _owned_active_sessions(user):
    user_id = str(user.id)
    for session in Session.objects.filter(expire_date__gte=timezone.now()).order_by("-expire_date"):
        try:
            if session.get_decoded().get("_auth_user_id") == user_id:
                yield session
        except Exception:
            continue


class SafeSessionListView(APIView):
    def get(self, request):
        current_key = request.session.session_key
        return Response(
            [
                {
                    "session_id": session_public_id(session.session_key),
                    "current": session.session_key == current_key,
                    "expire_date": session.expire_date,
                }
                for session in _owned_active_sessions(request.user)
            ]
        )


class SafeSessionRevokeView(APIView):
    def post(self, request, session_id):
        for session in _owned_active_sessions(request.user):
            if constant_time_compare(session_public_id(session.session_key), session_id):
                session.delete()
                return Response(status=204)
        # Do not reveal whether the opaque identifier belongs to another user or is unknown.
        return Response(status=204)
