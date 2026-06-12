import re
from .base import BaseEvaluator, EvaluationResult


class LLMJudgeEvaluator(BaseEvaluator):
    async def evaluate_async(self, response: str, config: dict, judge) -> EvaluationResult:
        if judge is None:
            return EvaluationResult(0, "未配置裁判模型")
        standard = config.get("standard", "无评分标准")
        prompt = f"""
你是一个回答评分器，需要根据给定标准对 AI 的回答评分。

[评判要求]
{standard}

[AI 的回答]
{response}

请严格按下面格式输出，不要包含其他内容：
Score: [0-100之间的分数]
Reason: [简短理由]
""".strip()
        judge_text = await judge(prompt)
        score_match = re.search(r"Score:\s*(\d+(?:\.\d+)?)", judge_text)
        reason_match = re.search(r"Reason:\s*(.*)", judge_text, re.DOTALL)
        if not score_match:
            return EvaluationResult(0, "无法解析裁判输出", judge_text)
        score = float(score_match.group(1))
        reason = reason_match.group(1).strip() if reason_match else ""
        return EvaluationResult(score, reason, judge_text)
