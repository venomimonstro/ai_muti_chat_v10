from rest_framework.routers import DefaultRouter

from .views import EvalCaseViewSet, EvalRunViewSet, ModelScoreViewSet

router = DefaultRouter()
router.register("eval-cases", EvalCaseViewSet, basename="eval-case")
router.register("eval-runs", EvalRunViewSet, basename="eval-run")
router.register("model-scores", ModelScoreViewSet, basename="model-score")

urlpatterns = router.urls
