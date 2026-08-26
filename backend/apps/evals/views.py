from rest_framework import mixins, viewsets

from apps.admin_ops.permissions import IsPlatformAdmin

from .models import EvalCase, EvalRun, ModelScore
from .serializers import (
    EvalCaseSerializer,
    EvalRunListSerializer,
    EvalRunSerializer,
    ModelScoreSerializer,
)


class EvalCaseViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsPlatformAdmin]
    serializer_class = EvalCaseSerializer

    def get_queryset(self):
        queryset = EvalCase.objects.all()
        dataset = self.request.query_params.get("dataset")
        taxonomy = self.request.query_params.get("taxonomy")
        if dataset:
            queryset = queryset.filter(dataset_version=dataset)
        if taxonomy:
            queryset = queryset.filter(taxonomy=taxonomy)
        return queryset


class EvalRunViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsPlatformAdmin]
    serializer_class = EvalRunSerializer

    def get_serializer_class(self):
        return EvalRunSerializer if self.action == "retrieve" else EvalRunListSerializer

    def get_queryset(self):
        queryset = EvalRun.objects.select_related("model", "baseline")
        if self.action == "retrieve":
            queryset = queryset.prefetch_related("results__case")
        model = self.request.query_params.get("model")
        dataset = self.request.query_params.get("dataset")
        if model:
            queryset = queryset.filter(model__slug=model)
        if dataset:
            queryset = queryset.filter(dataset_version=dataset)
        return queryset


class ModelScoreViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsPlatformAdmin]
    serializer_class = ModelScoreSerializer

    def get_queryset(self):
        queryset = ModelScore.objects.select_related("model", "run")
        model = self.request.query_params.get("model")
        taxonomy = self.request.query_params.get("taxonomy")
        if model:
            queryset = queryset.filter(model__slug=model)
        if taxonomy:
            queryset = queryset.filter(taxonomy=taxonomy)
        return queryset
