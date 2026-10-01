from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.chat import reasoning_policy


class ReasoningPolicyTests(SimpleTestCase):
    def _modules(self):
        def assemble_context(*args, **kwargs):
            return (
                {
                    "provider_messages": [
                        {"role": "system", "content": "base policy"},
                        {"role": "user", "content": kwargs.get("query", "")},
                    ],
                    "components": [],
                    "budget": {
                        "input_tokens": 20,
                        "remaining": 1000,
                    },
                    "sha256": "old",
                },
                [],
            )

        context = SimpleNamespace(
            assemble_context=assemble_context,
            estimate_tokens=lambda value: max(1, len(value) // 4),
        )
        streaming = SimpleNamespace(assemble_context=assemble_context)
        return context, streaming

    def test_complex_request_uses_deep_policy_in_real_streaming_symbol(self):
        context, streaming = self._modules()
        reasoning_policy.install(context, streaming)
        payload, _ = streaming.assemble_context(
            query=(
                "Проведи детальный аудит архитектуры и найди баги маршрутизации, "
                "сравни варианты исправления, риски и экономику реализации."
            )
        )
        self.assertEqual(payload["reasoning_mode"], "deep")
        self.assertIn("РЕЖИМ УГЛУБЛЕННОГО АНАЛИЗА", payload["provider_messages"][0]["content"])
        self.assertTrue(any(item["kind"] == "reasoning_policy" for item in payload["components"]))
        self.assertNotEqual(payload["sha256"], "old")

    def test_simple_request_stays_standard(self):
        context, streaming = self._modules()
        reasoning_policy.install(context, streaming)
        payload, _ = streaming.assemble_context(query="Что такое SEO?")
        self.assertEqual(payload["reasoning_mode"], "standard")
        self.assertNotIn("РЕЖИМ УГЛУБЛЕННОГО АНАЛИЗА", payload["provider_messages"][0]["content"])

    def test_budget_limit_does_not_overflow_context(self):
        context, streaming = self._modules()
        raw = context.assemble_context

        def small_budget(*args, **kwargs):
            payload, memories = raw(*args, **kwargs)
            payload["budget"]["remaining"] = 1
            return payload, memories

        context.assemble_context = small_budget
        streaming.assemble_context = small_budget
        reasoning_policy.install(context, streaming)
        payload, _ = streaming.assemble_context(
            query="Проведи детальный аудит архитектуры, сравни риски и варианты исправления."
        )
        self.assertEqual(payload["reasoning_mode"], "standard_budget_limited")
