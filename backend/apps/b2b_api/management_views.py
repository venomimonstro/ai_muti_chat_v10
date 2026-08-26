import uuid

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.text import slugify
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .keys import issue_key, revoke_key
from .models import APIKey, Organization, OrganizationMembership
from .serializers import APIKeyCreateSerializer, APIKeySerializer, OrganizationSerializer
from .services import usage_summary

KEY_MANAGERS = {OrganizationMembership.Role.OWNER, OrganizationMembership.Role.ADMIN}
USAGE_VIEWERS = KEY_MANAGERS | {
    OrganizationMembership.Role.BILLING,
    OrganizationMembership.Role.DEVELOPER,
    OrganizationMembership.Role.VIEWER,
}


def _membership(user, organization_id, roles=None):
    queryset = OrganizationMembership.objects.select_related("organization").filter(
        organization_id=organization_id,
        user=user,
        status=OrganizationMembership.Status.ACTIVE,
    )
    if roles:
        queryset = queryset.filter(role__in=roles)
    return get_object_or_404(queryset)


class OrganizationListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organizations = (
            Organization.objects.filter(
                memberships__user=request.user,
                memberships__status=OrganizationMembership.Status.ACTIVE,
            )
            .prefetch_related("memberships")
            .distinct()
        )
        return Response(
            OrganizationSerializer(
                organizations, many=True, context={"request": request}
            ).data
        )

    @transaction.atomic
    def post(self, request):
        serializer = OrganizationSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        base = slugify(serializer.validated_data["name"], allow_unicode=False) or "organization"
        # A random suffix avoids the check-then-insert race between concurrent requests.
        slug = f"{base}-{uuid.uuid4().hex[:12]}"
        organization = Organization.objects.create(
            name=serializer.validated_data["name"],
            slug=slug,
            billing_user=request.user,
            monthly_limit_rub=serializer.validated_data.get("monthly_limit_rub"),
        )
        OrganizationMembership.objects.create(
            organization=organization,
            user=request.user,
            role=OrganizationMembership.Role.OWNER,
        )
        return Response(
            OrganizationSerializer(organization, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class OrganizationKeyListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, organization_id):
        membership = _membership(request.user, organization_id, USAGE_VIEWERS)
        return Response(APIKeySerializer(membership.organization.api_keys.all(), many=True).data)

    def post(self, request, organization_id):
        membership = _membership(request.user, organization_id, KEY_MANAGERS)
        serializer = APIKeyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key, secret = issue_key(
            organization=membership.organization,
            actor=request.user,
            **serializer.validated_data,
        )
        data = APIKeySerializer(key).data
        data["secret"] = secret
        data["secret_visible_once"] = True
        return Response(data, status=status.HTTP_201_CREATED)


class OrganizationKeyRevokeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, organization_id, key_id):
        membership = _membership(request.user, organization_id, KEY_MANAGERS)
        key = get_object_or_404(APIKey, pk=key_id, organization=membership.organization)
        return Response(APIKeySerializer(revoke_key(key, actor=request.user)).data)


class OrganizationKeyRotateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, organization_id, key_id):
        membership = _membership(request.user, organization_id, KEY_MANAGERS)
        old = get_object_or_404(APIKey, pk=key_id, organization=membership.organization)
        revoke_key(old, actor=request.user)
        new, secret = issue_key(
            organization=membership.organization,
            actor=request.user,
            name=f"{old.name} (rotated)",
            scopes=old.scopes,
            allowed_models=old.allowed_models,
            allowed_endpoints=old.allowed_endpoints,
            monthly_limit_rub=old.monthly_limit_rub,
            rate_limit_per_minute=old.rate_limit_per_minute,
            max_concurrency=old.max_concurrency,
            ip_allowlist=old.ip_allowlist,
            expires_at=old.expires_at,
        )
        data = APIKeySerializer(new).data
        data["secret"] = secret
        data["secret_visible_once"] = True
        return Response(data, status=status.HTTP_201_CREATED)


class OrganizationUsageView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, organization_id):
        membership = _membership(request.user, organization_id, USAGE_VIEWERS)
        return Response(usage_summary(membership.organization))
