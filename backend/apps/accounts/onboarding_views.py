from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chat.models import Generation
from apps.files.models import FileAsset
from apps.payments.models import Payment
from apps.projects.models import Project


class OnboardingStatusView(APIView):
    def get(self, request):
        user = request.user
        completed_generations = Generation.objects.filter(
            owner=user, state=Generation.State.COMPLETED
        ).count()
        paid = Payment.objects.filter(user=user, status=Payment.Status.SUCCEEDED).exists()
        project_created = Project.objects.filter(owner=user).exists()
        file_ready = FileAsset.objects.filter(
            owner=user,
            status__in=[FileAsset.Status.READY, FileAsset.Status.PARTIAL],
            deleted_at__isnull=True,
        ).exists()
        steps = [
            {"key": "verify_email", "title": "Подтвердить email", "done": user.email_verified},
            {"key": "first_prompt", "title": "Получить первый AI-ответ", "done": completed_generations > 0},
            {"key": "project", "title": "Создать проект", "done": project_created},
            {"key": "file", "title": "Добавить документ", "done": file_ready},
            {"key": "payment", "title": "Пополнить баланс", "done": paid},
        ]
        core_done = user.email_verified and completed_generations > 0
        return Response(
            {
                "activated": core_done,
                "completed_generations": completed_generations,
                "has_paid": paid,
                "steps": steps,
                "progress_percent": round(sum(int(item["done"]) for item in steps) / len(steps) * 100),
                "recommended_mode": "balanced",
                "starter_prompts": [
                    "Сравни варианты решения и объясни риски",
                    "Проанализируй документ и выдели главное",
                    "Помоги написать и проверить код",
                    "Найди слабые места в моей идее и предложи улучшения",
                ],
            }
        )
