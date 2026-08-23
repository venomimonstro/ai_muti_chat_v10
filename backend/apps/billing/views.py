from rest_framework.response import Response
from rest_framework.views import APIView


class WalletView(APIView):
    def get(self, request):
        wallet = request.user.wallet
        entries = wallet.entries.order_by("-created_at")[:50]
        return Response(
            {
                "available_rub": wallet.available_rub,
                "reserved_rub": wallet.reserved_rub,
                "entries": [
                    {
                        "id": e.id,
                        "kind": e.kind,
                        "amount_rub": e.amount_rub,
                        "available_delta_rub": e.available_delta_rub,
                        "reserved_delta_rub": e.reserved_delta_rub,
                        "created_at": e.created_at,
                    }
                    for e in entries
                ],
            }
        )
