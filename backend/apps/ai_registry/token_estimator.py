import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    method: str


_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_CODE_HINT_RE = re.compile(r"[{};=<>]|\b(def|class|function|const|let|var|import|SELECT|FROM|WHERE)\b")


class TokenEstimator:
    """Conservative provider-agnostic estimator used only for preflight/reservations.

    Final billing must continue to use provider-reported usage. The estimator deliberately
    errs slightly high, while avoiding the old 1-character == 1-token behaviour.
    """

    def estimate_text(self, value: str) -> TokenEstimate:
        if not value:
            return TokenEstimate(tokens=0, method="empty")
        length = len(value)
        cyrillic = len(_CYRILLIC_RE.findall(value))
        cyrillic_ratio = cyrillic / max(length, 1)
        looks_like_code = bool(_CODE_HINT_RE.search(value))

        # Empirical safe coefficients for common BPE-family tokenizers.
        chars_per_token = 2.4 if cyrillic_ratio >= 0.20 else 3.4 if looks_like_code else 3.8
        tokens = max(1, math.ceil(length / chars_per_token))
        # Small message framing/safety allowance without multiplying large prompts.
        tokens += max(2, math.ceil(tokens * 0.06))
        return TokenEstimate(tokens=tokens, method="calibrated-v1")

    def estimate_messages(self, messages: list[dict]) -> TokenEstimate:
        total = 0
        for item in messages:
            content = item.get("content", "")
            if isinstance(content, str):
                total += self.estimate_text(content).tokens
            else:
                total += self.estimate_text(str(content)).tokens
            total += 4  # role/framing allowance
        return TokenEstimate(tokens=max(total, 1), method="calibrated-v1-messages")


DEFAULT_TOKEN_ESTIMATOR = TokenEstimator()


def estimate_text_tokens(value: str) -> int:
    return DEFAULT_TOKEN_ESTIMATOR.estimate_text(value).tokens


def estimate_message_tokens(messages: list[dict]) -> int:
    return DEFAULT_TOKEN_ESTIMATOR.estimate_messages(messages).tokens
