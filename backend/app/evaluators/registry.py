from .deterministic import ExactMatchEvaluator, FillInEvaluator, ContainsAllEvaluator
from .llm_judge import LLMJudgeEvaluator


def get_evaluator(method: str):
    if method == "exact_match":
        return ExactMatchEvaluator()
    if method == "fill_in":
        return FillInEvaluator()
    if method in {"contains_all", "contains"}:
        return ContainsAllEvaluator()
    if method == "llm_eval":
        return LLMJudgeEvaluator()
    return LLMJudgeEvaluator()
