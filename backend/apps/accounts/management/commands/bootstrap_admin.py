import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Create the initial platform administrator without exposing the password in arguments"

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", required=True)
        parser.add_argument(
            "--reset-password",
            action="store_true",
            help="Reset the password when resuming the trusted server installer",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        password = os.getenv("AIWORKSPACE_ADMIN_PASSWORD", "")
        if len(password) < 12:
            raise CommandError("AIWORKSPACE_ADMIN_PASSWORD must contain at least 12 characters")
        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(
            username=options["username"],
            defaults={"email": options["email"].strip().casefold()},
        )
        if not created:
            if not user.is_superuser:
                raise CommandError("Username already exists and is not a superuser")
            if options["reset_password"]:
                user.email = options["email"].strip().casefold()
                user.role = user_model.Role.PLATFORM_ADMIN
                user.is_staff = True
                user.is_active = True
                user.set_password(password)
                user.save()
                self.stdout.write(self.style.SUCCESS("Platform administrator refreshed"))
                return
            self.stdout.write("Administrator already exists; password was not changed")
            return
        user.email = options["email"].strip().casefold()
        user.role = user_model.Role.PLATFORM_ADMIN
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS("Platform administrator created"))
