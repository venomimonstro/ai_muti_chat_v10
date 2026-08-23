from django.db.models import Q

from .models import Project, ProjectMembership


def accessible_projects(user, *, write=False):
    membership_roles = [ProjectMembership.Role.OWNER, ProjectMembership.Role.EDITOR]
    membership_filter = Q(memberships__user=user)
    if write:
        membership_filter &= Q(memberships__role__in=membership_roles)
    return Project.objects.filter(Q(owner=user) | membership_filter).distinct()
