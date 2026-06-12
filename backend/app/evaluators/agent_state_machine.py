from __future__ import annotations

import json
from typing import Any

from .base import BaseEvaluator, EvaluationResult
from .agent_trace import _argument_matches
from backend.app.services.command_policy import default_command_policy, summarize_command_policy


def _tool_calls(trace: dict[str, Any]) -> list[dict[str, Any]]:
    return trace.get("tool_trace") or []


def _final_answer_contains(response: str, needles: list[str]) -> bool:
    return all(str(needle) in response for needle in (needles or []))


def _argument_contains(actual: dict[str, Any], expected_contains: dict[str, Any]) -> bool:
    for key, expected in (expected_contains or {}).items():
        actual_value = str(actual.get(key, ""))
        needles = expected if isinstance(expected, list) else [expected]
        if not all(str(needle) in actual_value for needle in needles):
            return False
    return True


def _match_tool_call(tool_trace: list[dict[str, Any]], rule: dict[str, Any]) -> bool:
    name = rule.get("name") or rule.get("tool_name")
    name_in = set(rule.get("name_in") or [])
    expected_args = rule.get("arguments") or {}
    expected_arg_contains = rule.get("arguments_contains") or {}
    for call in tool_trace:
        call_name = call.get("tool_name") or call.get("name")
        if name and call_name != name:
            continue
        if name_in and call_name not in name_in:
            continue
        call_args = call.get("arguments") or {}
        if expected_args and not _argument_matches(call_args, expected_args):
            continue
        if expected_arg_contains and not _argument_contains(call_args, expected_arg_contains):
            continue
        status = rule.get("status")
        if status and call.get("status") != status:
            continue
        return True
    return False


def _contains_forbidden_tool(tool_trace: list[dict[str, Any]], rules: list[dict[str, Any]]) -> bool:
    return any(_match_tool_call(tool_trace, rule) for rule in rules or [])


def _allowed_skill_names(config: dict[str, Any]) -> set[str]:
    allowed = set(config.get("state_machine", {}).get("allowed_skills") or [])
    if allowed:
        return allowed
    required = config.get("expected_trace", {}).get("required_tool_calls") or []
    allowed = {((rule.get("arguments") or {}).get("name")) for rule in required if rule.get("tool_name") == "skill_view"}
    allowed.discard(None)
    return allowed


def _skill_call_health(tool_trace: list[dict[str, Any]], config: dict[str, Any]) -> tuple[bool, bool]:
    allowed = _allowed_skill_names(config)
    if not allowed:
        return False, False
    has_irrelevant = False
    loaded_allowed = False
    for call in tool_trace:
        if call.get("tool_name") != "skill_view":
            continue
        skill_name = (call.get("arguments") or {}).get("name")
        if skill_name in allowed and call.get("status") == "success":
            loaded_allowed = True
        elif skill_name not in allowed:
            has_irrelevant = True
    return has_irrelevant, loaded_allowed


def evaluate_predicate(predicate: Any, facts: dict[str, bool], trace: dict[str, Any], response: str, config: dict[str, Any]) -> bool:
    if predicate is None:
        return False
    if isinstance(predicate, bool):
        return predicate
    if isinstance(predicate, str):
        return bool(facts.get(predicate))
    if isinstance(predicate, list):
        return all(evaluate_predicate(item, facts, trace, response, config) for item in predicate)
    if not isinstance(predicate, dict):
        return False

    if "all" in predicate:
        return all(evaluate_predicate(item, facts, trace, response, config) for item in predicate.get("all") or [])
    if "any" in predicate:
        return any(evaluate_predicate(item, facts, trace, response, config) for item in predicate.get("any") or [])
    if "not" in predicate:
        return not evaluate_predicate(predicate.get("not"), facts, trace, response, config)

    if "fact" in predicate:
        return bool(facts.get(str(predicate.get("fact"))))
    if "tool_call" in predicate:
        return _match_tool_call(_tool_calls(trace), predicate.get("tool_call") or {})
    if "any_tool_call" in predicate:
        return _match_tool_call(_tool_calls(trace), predicate.get("any_tool_call") or {})
    if "final_answer_contains" in predicate:
        return _final_answer_contains(response, predicate.get("final_answer_contains") or [])
    if "assistant_done" in predicate:
        expected = bool(predicate.get("assistant_done"))
        return facts.get("assistant_done", False) is expected
    if "metric" in predicate:
        metric_rule = predicate.get("metric") or {}
        metrics = trace.get("metrics") or {}
        value = metrics.get(metric_rule.get("name"))
        if "equals" in metric_rule:
            return value == metric_rule.get("equals")
        if "lte" in metric_rule:
            return value is not None and value <= metric_rule.get("lte")
        if "gte" in metric_rule:
            return value is not None and value >= metric_rule.get("gte")
    if "forbidden_or_irrelevant_skill" in predicate:
        if not predicate.get("forbidden_or_irrelevant_skill"):
            return False
        has_irrelevant, loaded_allowed = _skill_call_health(_tool_calls(trace), config)
        return has_irrelevant and not loaded_allowed

    # Shorthand: {some_fact: true}
    if len(predicate) == 1:
        key, expected = next(iter(predicate.items()))
        if key in facts:
            return bool(facts[key]) is bool(expected)
    return False


def compile_facts(trace: dict[str, Any], response: str, config: dict[str, Any]) -> dict[str, bool]:
    state_machine = config.get("state_machine") or {}
    metrics = trace.get("metrics") or {}
    agent = config.get("agent") or {}
    tool_trace = _tool_calls(trace)
    facts: dict[str, bool] = {
        "assistant_done": bool(response.strip()) or metrics.get("stop_reason") == "final_answer",
        "tool_budget_ok": int(metrics.get("tool_calls_total", len(tool_trace)) or 0) <= int(agent.get("max_tool_calls", 10**9)),
        "has_tool_error": any(call.get("status") in {"error", "denied", "timeout"} for call in tool_trace),
        "safety_violation": bool(metrics.get("safety_violation")),
    }
    expected = config.get("expected_trace") or {}
    facts["called_forbidden_tool"] = _contains_forbidden_tool(tool_trace, expected.get("forbidden_tool_calls") or [])
    command_summary = summarize_command_policy(trace, response, config)
    facts["command_policy_enabled"] = bool(command_summary.get("enabled"))
    facts["has_dangerous_command"] = command_summary.get("status") == "dangerous"
    facts["has_invalid_command"] = command_summary.get("status") == "invalid"
    has_irrelevant, loaded_allowed = _skill_call_health(tool_trace, config)
    facts["has_irrelevant_skill_call"] = has_irrelevant
    facts["recovered_from_irrelevant_skill_call"] = has_irrelevant and loaded_allowed
    for _ in range(max(1, len(state_machine.get("facts") or {}) + 1)):
        changed = False
        for name, predicate in (state_machine.get("facts") or {}).items():
            value = evaluate_predicate(predicate, facts, trace, response, config)
            if facts.get(name) is not value:
                facts[name] = value
                changed = True
        if not changed:
            break
    return facts


def run_state_machine(facts: dict[str, bool], trace: dict[str, Any], response: str, config: dict[str, Any]) -> dict[str, Any]:
    sm = config.get("state_machine") or {}
    states = sm.get("states") or {}
    current = sm.get("initial", "start")
    path = [current]
    matched: list[dict[str, Any]] = []
    max_steps = int(sm.get("max_steps", max(1, len(states) + 3)))

    for _ in range(max_steps):
        state = states.get(current) or {}
        if state.get("terminal"):
            return {
                "terminal_state": current,
                "path": path,
                "score": float(state.get("score", 0)),
                "reason": state.get("reason", f"Reached terminal state {current}"),
                "matched_transitions": matched,
                "matched_facts": facts,
            }
        transitioned = False
        for idx, transition in enumerate(state.get("transitions") or []):
            if evaluate_predicate(transition.get("when"), facts, trace, response, config):
                target = transition.get("to", current)
                matched.append({"from": current, "to": target, "index": idx})
                current = target
                path.append(current)
                transitioned = True
                if transition.get("terminal"):
                    return {
                        "terminal_state": current,
                        "path": path,
                        "score": float(transition.get("score", 0)),
                        "reason": transition.get("reason", f"Transitioned to terminal state {current}"),
                        "matched_transitions": matched,
                        "matched_facts": facts,
                    }
                break
        if not transitioned:
            fallback_score = float(state.get("default_score", sm.get("default_score", 0)))
            return {
                "terminal_state": state.get("default_terminal_state", current),
                "path": path,
                "score": fallback_score,
                "reason": state.get("default_reason", f"No transition matched from state {current}"),
                "matched_transitions": matched,
                "matched_facts": facts,
            }
    return {
        "terminal_state": current,
        "path": path,
        "score": float(sm.get("loop_score", 0)),
        "reason": "State machine exceeded max_steps; possible loop",
        "matched_transitions": matched,
        "matched_facts": facts,
    }


class AgentStateMachineEvaluator(BaseEvaluator):
    def evaluate(self, response: str, config: dict, judge=None) -> EvaluationResult:
        trace = config.get("trace") or {}
        command_summary = summarize_command_policy(trace, response, config)
        facts = compile_facts(trace, response, config)
        if command_summary.get("status") in {"dangerous", "invalid"}:
            policy = default_command_policy(config)
            status = command_summary["status"]
            state_result = {
                "terminal_state": f"{status}_command_pruned",
                "path": [f"{status}_command_pruned"],
                "score": float(policy["dangerous_score"] if status == "dangerous" else policy["invalid_score"]),
                "reason": f"Command policy rejected {status} command.",
                "matched_transitions": [],
                "matched_facts": facts,
                "command_policy": command_summary,
            }
        else:
            state_result = run_state_machine(facts, trace, response, config)
            if command_summary.get("enabled"):
                state_result["command_policy"] = command_summary
        score = round(max(0.0, min(float(state_result.get("score", 0.0)), 100.0)), 2)
        state_result["score"] = score
        raw = json.dumps({"state_machine": state_result, "trace": trace}, ensure_ascii=False)
        reason = state_result.get("reason") or f"terminal_state={state_result.get('terminal_state')}"
        return EvaluationResult(score, reason, raw)
