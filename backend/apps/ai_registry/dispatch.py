"""Fail-safe provider adapter dispatch.

Slug-routed production providers must never fall through to the internal Echo
adapter even when a legacy database row contains adapter_type=echo.
"""

import os

from . import adapters
from .models import Provider


def adapter_for(model):
    provider = model.provider
    api_key = provider.get_api_key()

    # Slug-specific production adapters are authoritative and deliberately run
    # before adapter_type. This prevents legacy GigaChat/OpenRouter rows from
    # silently returning Echo responses.
    if provider.slug == "gigachat":
        from .gigachat_adapter import GigaChatAPIAdapter

        return GigaChatAPIAdapter(
            authorization_key=api_key,
            base_url=provider.api_base_url
            or os.getenv("GIGACHAT_API_BASE_URL", "https://api.giga.chat/v1"),
            scope=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
        )
    if provider.slug == "openrouter":
        return adapters.OpenRouterChatAdapter(
            api_key=api_key,
            base_url=provider.api_base_url
            or os.getenv("OPENROUTER_API_BASE_URL", "https://openrouter.ai/api/v1"),
        )

    if provider.adapter_type == Provider.AdapterType.ECHO:
        return adapters.EchoProviderAdapter()
    if provider.adapter_type == Provider.AdapterType.OPENAI_RESPONSES:
        return adapters.OpenAIResponsesAdapter(
            api_key=api_key,
            base_url=provider.api_base_url
            or os.getenv("OPENAI_API_BASE_URL", "https://api.openai.com/v1"),
        )
    if provider.adapter_type == Provider.AdapterType.ANTHROPIC_MESSAGES:
        return adapters.AnthropicMessagesAdapter(
            api_key=api_key,
            base_url=provider.api_base_url
            or os.getenv("ANTHROPIC_API_BASE_URL", "https://api.anthropic.com/v1"),
        )
    if provider.adapter_type == Provider.AdapterType.DEEPSEEK_CHAT:
        return adapters.DeepSeekChatAdapter(
            api_key=api_key,
            base_url=provider.api_base_url
            or os.getenv("DEEPSEEK_API_BASE_URL", "https://api.deepseek.com"),
        )
    if provider.adapter_type == Provider.AdapterType.GEMINI_GENERATE_CONTENT:
        return adapters.GeminiGenerateContentAdapter(
            api_key=api_key,
            base_url=provider.api_base_url
            or os.getenv("GEMINI_API_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"),
        )
    if provider.adapter_type == Provider.AdapterType.XAI_CHAT:
        return adapters.XAIChatAdapter(
            api_key=api_key,
            base_url=provider.api_base_url
            or os.getenv("XAI_API_BASE_URL", "https://api.x.ai/v1"),
        )
    raise adapters.ProviderError(
        f"Unsupported adapter: {provider.adapter_type}",
        code="unsupported_adapter",
        retryable=False,
    )
