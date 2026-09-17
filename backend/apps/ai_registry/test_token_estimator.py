from apps.ai_registry.token_estimator import estimate_message_tokens, estimate_text_tokens


def test_english_is_not_counted_one_character_per_token():
    text = "This is a normal English sentence used for token estimation. " * 20
    estimate = estimate_text_tokens(text)
    assert estimate > 0
    assert estimate < len(text) // 2


def test_russian_is_more_conservative_than_english():
    ru = "Это обычное русское предложение для проверки оценки токенов. " * 20
    en = "This is a normal English sentence used for token estimation. " * 20
    assert estimate_text_tokens(ru) > estimate_text_tokens(en)
    assert estimate_text_tokens(ru) < len(ru)


def test_code_gets_conservative_estimate():
    code = "def calculate(value):\n    return value * 2\n" * 100
    assert 0 < estimate_text_tokens(code) < len(code)


def test_messages_include_framing_allowance():
    messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "world"}]
    assert estimate_message_tokens(messages) > estimate_text_tokens("helloworld")
