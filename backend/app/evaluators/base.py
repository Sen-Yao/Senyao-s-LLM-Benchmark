from dataclasses import dataclass


@dataclass
class EvaluationResult:
    score: float
    reason: str
    raw: str = ""


class BaseEvaluator:
    def evaluate(self, response: str, config: dict, judge=None) -> EvaluationResult:
        raise NotImplementedError
