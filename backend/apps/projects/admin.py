from django.contrib import admin

from .models import Project, ProjectInstruction, ProjectMembership

admin.site.register(Project)
admin.site.register(ProjectInstruction)
admin.site.register(ProjectMembership)
