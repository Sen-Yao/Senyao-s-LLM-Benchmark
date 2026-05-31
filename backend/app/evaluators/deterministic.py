import re
from .base import BaseEvaluator, EvaluationResult


def _norm(text: str) -> str:
    return str(text).strip().lower().replace('"', '').replace("'", "")


class ExactMatchEvaluator(BaseEvaluator):
    def evaluate(self, response: str, config: dict, judge=None) -> EvaluationResult:
        mapping = config.get("mapping", {})
        processed = _norm(response)
        is_choice = mapping and all(len(str(k)) == 1 for k in mapping)
        if is_choice:
            match = re.search(r"[a-zA-Z]", processed)
            if match:
                processed = match.group().lower()
        if processed in mapping:
            result = mapping[processed]
            if isinstance(result, dict):
                return EvaluationResult(float(result.get("score", 0)), result.get("reason", "匹配成功"))
            return EvaluationResult(float(result), "回答正确")
        return EvaluationResult(float(config.get("default_score", 0)), config.get("default_reason", "回答未匹配"))


class FillInEvaluator(BaseEvaluator):
    def evaluate(self, response: str, config: dict, judge=None) -> EvaluationResult:
        answers = config.get("answers", [])
        if isinstance(answers, str):
            answers = [answers]
        res = re.sub(r"[。，！？．\.!\?,]$", "", _norm(response))
        normalized = [_norm(a) for a in answers]
        if res in normalized:
            return EvaluationResult(float(config.get("score", 100)), "回答正确")
        return EvaluationResult(float(config.get("default_score", 0)), config.get("default_reason", "回答错误"))


class ContainsAllEvaluator(BaseEvaluator):
    def evaluate(self, response: str, config: dict, judge=None) -> EvaluationResult:
        needles = config.get("contains_all", [])
        missing = [n for n in needles if n not in response]
        if not missing:
            return EvaluationResult(float(config.get("score", 100)), "包含所有关键点")
        return EvaluationResult(float(config.get("default_score", 0)), "缺少关键点：" + "、".join(missing))
