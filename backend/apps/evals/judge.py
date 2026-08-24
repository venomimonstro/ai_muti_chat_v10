from dataclasses import dataclass


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _coverage(response: str, required: list[str]) -> float:
    if not required:
        return 1.0
    normalized = _normalize(response)
    return sum(_normalize(item) in normalized for item in required) / len(required)


@dataclass(frozen=True)
class JudgeScore:
    score: float
    correctness: float
    instruction_following: float
    russian_quality: float
    verbosity_control: float
    hallucinated: bool
    details: dict


def score_response(response: str, rubric: dict) -> JudgeScore:
    normalized = _normalize(response)
    exact = rubric.get("exact_answer")
    required = rubric.get("required_phrases", [])
    required_any = rubric.get("required_any", [])
    forbidden = rubric.get("forbidden_phrases", [])
    sections = rubric.get("required_sections", [])
    max_chars = int(rubric.get("max_chars", 0) or 0)

    correctness = float(normalized == _normalize(exact)) if exact else _coverage(response, required)
    if required_any:
        correctness *= float(any(_normalize(item) in normalized for item in required_any))

    section_score = _coverage(response, sections)
    verbosity = 1.0 if not max_chars or len(response) <= max_chars else max_chars / len(response)
    instruction = (section_score + verbosity) / 2

    letters = [char for char in response.casefold() if char.isalpha()]
    cyrillic = [char for char in letters if "а" <= char <= "я" or char == "ё"]
    russian_ratio = len(cyrillic) / len(letters) if letters else 0
    russian_required = rubric.get("russian_required", True)
    russian_quality = 1.0 if not russian_required else min(1.0, russian_ratio / 0.75)

    matched_forbidden = [item for item in forbidden if _normalize(item) in normalized]
    hallucinated = bool(matched_forbidden)
    weights = rubric.get(
        "weights",
        {
            "correctness": 0.45,
            "instruction_following": 0.25,
            "russian_quality": 0.20,
            "verbosity_control": 0.10,
        },
    )
    score = (
        correctness * float(weights.get("correctness", 0))
        + instruction * float(weights.get("instruction_following", 0))
        + russian_quality * float(weights.get("russian_quality", 0))
        + verbosity * float(weights.get("verbosity_control", 0))
    )
    if hallucinated:
        score *= float(rubric.get("hallucination_penalty", 0.25))
    return JudgeScore(
        score=max(0.0, min(1.0, score)),
        correctness=correctness,
        instruction_following=instruction,
        russian_quality=russian_quality,
        verbosity_control=verbosity,
        hallucinated=hallucinated,
        details={
            "judge": "deterministic-rubric-v1",
            "required_coverage": correctness,
            "section_coverage": section_score,
            "russian_ratio": round(russian_ratio, 4),
            "matched_forbidden": matched_forbidden,
            "response_chars": len(response),
        },
    )
