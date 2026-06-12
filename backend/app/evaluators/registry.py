from .deterministic import ExactMatchEvaluator, FillInEvaluator, ContainsAllEvaluator
from .llm_judge import LLMJudgeEvaluator
from .agent_trace import AgentTraceEvaluator
from .agent_state_machine import AgentStateMachineEvaluator


def get_evaluator(method: str):
    if method == "exact_match":
        return ExactMatchEvaluator()
    if method == "fill_in":
        return FillInEvaluator()
    if method in {"contains_all", "contains"}:
        return ContainsAllEvaluator()
    if method == "agent_trace_eval":
        return AgentTraceEvaluator()
    if method == "agent_state_machine_eval":
        return AgentStateMachineEvaluator()
    if method == "llm_eval":
        return LLMJudgeEvaluator()
    return LLMJudgeEvaluator()
