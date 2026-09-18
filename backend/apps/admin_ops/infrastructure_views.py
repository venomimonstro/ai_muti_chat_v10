from rest_framework.response import Response
from rest_framework.views import APIView

from .infrastructure import infrastructure_health
from .permissions import IsPlatformAdmin


class InfrastructureHealthView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        return Response(infrastructure_health())
