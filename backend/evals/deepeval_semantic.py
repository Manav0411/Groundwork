from dataclasses import dataclass

from evals.models import EvaluationCase


@dataclass(frozen=True)
class SemanticResult:
    score: float
    reason: str


def evaluate_semantics(
    case: EvaluationCase,
    answer: str,
    *,
    model_name: str,
    base_url: str,
) -> SemanticResult:
    """Run optional LLM-as-judge scoring with a local Ollama model."""
    if not case.semantic_reference:
        raise ValueError(f"Case {case.id!r} does not define semantic_reference")
    try:
        from deepeval.metrics import GEval
        from deepeval.models import OllamaModel
        from deepeval.test_case import LLMTestCase, SingleTurnParams
    except ImportError as exc:
        raise RuntimeError(
            'DeepEval is optional. Install it with: pip install -e ".[dev,eval]"'
        ) from exc

    judge = OllamaModel(model=model_name, base_url=base_url, temperature=0)
    metric = GEval(
        name="Answer correctness",
        criteria=(
            "Determine whether the actual answer agrees with the reference answer. "
            "Do not penalize wording differences. Penalize invented commits, authors, or claims."
        ),
        evaluation_params=[
            SingleTurnParams.INPUT,
            SingleTurnParams.ACTUAL_OUTPUT,
            SingleTurnParams.EXPECTED_OUTPUT,
        ],
        model=judge,
        threshold=0.7,
    )
    test_case = LLMTestCase(
        input=case.query,
        actual_output=answer,
        expected_output=case.semantic_reference,
    )
    metric.measure(test_case)
    return SemanticResult(score=float(metric.score or 0), reason=metric.reason or "")
