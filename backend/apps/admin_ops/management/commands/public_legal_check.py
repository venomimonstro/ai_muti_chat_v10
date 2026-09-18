import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Verify public legal documents are published and not placeholder templates"

    PATHS = ("/legal/offer", "/legal/privacy", "/legal/refunds", "/legal/acceptable-use")
    FORBIDDEN = ("{{", "}}", "LEGAL_NAME", "TAX_ID", "PUBLIC_LEGAL_URL", "example.test")

    def handle(self,*args,**options):
        base=getattr(settings,"FRONTEND_PUBLIC_URL","").rstrip("/")
        if not base.startswith("https://"):
            raise CommandError("FRONTEND_PUBLIC_URL must be a production HTTPS URL")
        failures=[]
        with httpx.Client(timeout=10,follow_redirects=True) as client:
            for path in self.PATHS:
                try:
                    response=client.get(base+path)
                    text=response.text
                except Exception as exc:
                    failures.append(f"{path}: request failed: {exc}")
                    continue
                if response.status_code != 200:
                    failures.append(f"{path}: HTTP {response.status_code}")
                    continue
                if len(text.strip()) < 400:
                    failures.append(f"{path}: document is unexpectedly short")
                for marker in self.FORBIDDEN:
                    if marker in text:
                        failures.append(f"{path}: unresolved placeholder {marker}")
        if failures:
            raise CommandError("Public legal documents BLOCKED: "+"; ".join(failures))
        self.stdout.write(self.style.SUCCESS("PUBLIC LEGAL DOCUMENTS: PASS"))
