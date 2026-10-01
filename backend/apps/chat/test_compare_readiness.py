from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from apps.chat import compare_readiness


class CompareReadinessTests(SimpleTestCase):
    def _module(self, model):
        return SimpleNamespace(
            _models=lambda _slugs: [model],
            _one_model=lambda _slug: model,
        )

    def test_compare_rejects_model_that_is_not_client_ready(self):
        model = SimpleNamespace(slug="stale-model")
        module = self._module(model)
        with patch.object(compare_readiness, "model_client_ready", return_value=False):
            compare_readiness.install(module)
            with self.assertRaises(ValidationError):
                module._models([model.slug])
            with self.assertRaises(ValidationError):
                module._one_model(model.slug)

    def test_compare_keeps_client_ready_model(self):
        model = SimpleNamespace(slug="ready-model")
        module = self._module(model)
        with patch.object(compare_readiness, "model_client_ready", return_value=True):
            compare_readiness.install(module)
            self.assertEqual(module._models([model.slug]), [model])
            self.assertIs(module._one_model(model.slug), model)
