from django.test import SimpleTestCase

from .provider_views import _model_metadata, _polza_chat_model


class PolzaDiscoveryTests(SimpleTestCase):
    def test_filters_non_chat_endpoint_when_metadata_is_explicit(self):
        self.assertFalse(
            _polza_chat_model(
                {
                    "id": "openai/gpt-image-2",
                    "supported_endpoints": ["/images/generations"],
                },
                "openai/gpt-image-2",
            )
        )
        self.assertTrue(
            _polza_chat_model(
                {
                    "id": "anthropic/claude-sonnet-5.5",
                    "supported_endpoints": ["/chat/completions"],
                },
                "anthropic/claude-sonnet-5.5",
            )
        )

    def test_imports_context_output_and_multimodal_capabilities(self):
        meta = _model_metadata(
            {
                "context_length": 200000,
                "max_completion_tokens": 8192,
                "architecture": {
                    "input_modalities": ["text", "image"],
                },
                "capabilities": ["tools"],
            },
            "vendor/model",
        )
        self.assertEqual(meta["context_window"], 200000)
        self.assertEqual(meta["max_output_tokens"], 8192)
        self.assertIn("text", meta["capabilities"])
        self.assertIn("streaming", meta["capabilities"])
        self.assertIn("vision", meta["capabilities"])
        self.assertIn("tools", meta["capabilities"])
