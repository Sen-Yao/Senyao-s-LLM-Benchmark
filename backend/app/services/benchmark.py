import asyncio
import json
import uuid
from datetime import datetime
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session
from backend.app.adapters.openai_compatible import chat_completion, chat_completion_messages
from backend.app.services.agent_tools import build_tool_schemas, execute_fixture_tool
from backend.app.db import SessionLocal
from backend.app.evaluators.llm_judge import LLMJudgeEvaluator
from backend.app.evaluators.registry import get_evaluator
from backend.app.models import BenchmarkRun, JudgeProfile, LLMModel, Provider, Task, TaskResult
from backend.app.security.crypto import decrypt_secret


def _load_run_context(session: Session, model_id: int, task_slugs: list[str] | None, suite: str, run_id: str | None):
    model = session.get(LLMModel, model_id)
    if not model:
        raise ValueError("model not found")
    provider = session.get(Provider, model.provider_id)
    query = session.query(Task).filter(Task.active.is_not(False))
    if task_slugs:
        query = query.filter(Task.slug.in_(task_slugs))
    tasks = query.order_by(Task.category, Task.slug).all()
    run = session.query(BenchmarkRun).filter(BenchmarkRun.run_id == run_id).first() if run_id else None
    if run:
        run.status = "running"
        run.started_at = run.started_at or datetime.utcnow()
    else:
        run = BenchmarkRun(run_id=uuid.uuid4().hex[:16], model_id=model.id, suite_slug=suite, status="running", started_at=datetime.utcnow())
        session.add(run)
    session.commit()
    return model, provider, tasks, run


async def _run_agent_interaction(provider, api_key: str, model, task: Task, attempt_timeout: float):
    task_config = json.loads(task.config_json or "{}")
    agent_config = task_config.get("agent") or {}
    max_turns = int(agent_config.get("max_turns", 6))
    max_tool_calls = int(agent_config.get("max_tool_calls", 8))
    tools = build_tool_schemas(agent_config)
    messages = [{"role": "user", "content": task.prompt}]
    tool_trace = []
    assistant_trace = []
    total_latency = 0.0
    input_tokens = 0
    output_tokens = 0
    final_text = ""
    stop_reason = "max_turns"

    for _turn in range(max_turns):
        call = await chat_completion_messages(
            provider.api_base,
            api_key,
            model.model_id,
            messages,
            tools=tools,
            timeout=attempt_timeout,
            tool_protocol=model.tool_protocol,
        )
        total_latency += call.latency or 0.0
        input_tokens += call.input_tokens or 0
        output_tokens += call.output_tokens or 0
        assistant_trace.append(
            {
                "content": call.text or "",
                "tool_calls": [tc.raw or {"id": tc.id, "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}} for tc in call.tool_calls],
            }
        )
        if call.tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": call.text or "",
                    "tool_calls": [tc.raw or {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}} for tc in call.tool_calls],
                }
            )
            for tool_call in call.tool_calls:
                if len(tool_trace) >= max_tool_calls:
                    result = {
                        "call_id": tool_call.id,
                        "tool_name": tool_call.name,
                        "arguments": tool_call.arguments,
                        "status": "denied",
                        "observation": {"error_type": "budget_exceeded", "error_message": "max_tool_calls exceeded", "truncated": False},
                        "usage": {"returned_chars": 0, "raw_chars": 0},
                    }
                else:
                    result = execute_fixture_tool(tool_call, task_config)
                tool_trace.append(result)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": json.dumps(result.get("observation") or {}, ensure_ascii=False),
                    }
                )
            continue
        final_text = call.text or ""
        stop_reason = "final_answer"
        break

    expected = task_config.get("expected_trace") or {}
    required = expected.get("required_tool_calls") or []
    required_ok = True
    for req in required:
        matched = any(
            row.get("tool_name") == req.get("tool_name")
            and all((row.get("arguments") or {}).get(key) == value for key, value in (req.get("arguments") or {}).items())
            for row in tool_trace
        )
        required_ok = required_ok and matched
    metrics = {
        "tool_calls_total": len(tool_trace),
        "invalid_tool_calls": sum(1 for row in tool_trace if row.get("status") in {"error", "denied"}),
        "required_tool_calls_satisfied": required_ok,
        "tool_result_chars": sum((row.get("usage") or {}).get("returned_chars", 0) for row in tool_trace),
        "safety_violation": False,
        "stop_reason": stop_reason,
    }
    trace = {
        "messages": messages,
        "assistant_trace": assistant_trace,
        "tool_trace": tool_trace,
        "metrics": metrics,
    }
    return {
        "text": final_text,
        "trace": trace,
        "tool_metrics": metrics,
        "latency": round(total_latency, 3),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


async def run_model_tasks(
    session: Session,
    model_id: int,
    task_slugs: list[str] | None = None,
    judge_profile_id: int | None = None,
    suite: str = "all",
    run_id: str | None = None,
    max_concurrency: int = 1,
    max_retries: int = 3,
    force_rerun: bool = False,
    retry_backoff_base: float = 1.0,
    attempt_timeout: float = 120.0,
) -> BenchmarkRun:
    model, provider, tasks, run = _load_run_context(session, model_id, task_slugs, suite, run_id)
    api_key = decrypt_secret(provider.encrypted_api_key)

    judge_profile = session.get(JudgeProfile, judge_profile_id) if judge_profile_id else None
    judge_provider = session.get(Provider, judge_profile.provider_id) if judge_profile else None
    judge_key = decrypt_secret(judge_provider.encrypted_api_key) if judge_provider else None

    async def _judge(prompt: str) -> str:
        if not judge_profile or not judge_provider or not judge_key:
            raise ValueError("LLM judge requested but judge profile is not configured")
        result = await chat_completion(judge_provider.api_base, judge_key, judge_profile.model_id, prompt, temperature=judge_profile.temperature)
        return result.text

    async def _run_task(task_id: int):
        local = SessionLocal()
        try:
            task = local.get(Task, task_id)
            model_local = local.get(LLMModel, model_id)
            provider_local = local.get(Provider, model_local.provider_id)
            run_local = local.query(BenchmarkRun).filter(BenchmarkRun.run_id == run.run_id).first()
            if not task or not run_local or run_local.status == "cancelled":
                return
            result_row = (
                local.query(TaskResult)
                .filter(TaskResult.run_id == run.run_id, TaskResult.task_id == task.id)
                .order_by(TaskResult.id.asc())
                .first()
            )
            if result_row is None:
                result_row = TaskResult(
                    run_id=run.run_id,
                    model_id=model_id,
                    task_id=task.id,
                    task_hash=task.content_hash,
                    semantic_hash=task.semantic_hash or task.content_hash,
                    evaluator_version=task.evaluator_version or "v1",
                    prompt=task.prompt,
                    status="pending",
                )
                local.add(result_row)
                local.flush()
            else:
                result_row.task_hash = task.content_hash
                result_row.semantic_hash = task.semantic_hash or task.content_hash
                result_row.evaluator_version = task.evaluator_version or "v1"
            if result_row.status == "cancelled":
                local.commit()
                return
            previous_success = None
            if not force_rerun:
                previous_success = (
                    local.query(TaskResult)
                    .filter(
                        TaskResult.model_id == model_id,
                        TaskResult.task_id == task.id,
                        or_(
                            and_(
                                TaskResult.semantic_hash == (task.semantic_hash or task.content_hash),
                                TaskResult.evaluator_version == (task.evaluator_version or "v1"),
                            ),
                            and_(
                                TaskResult.semantic_hash == "",
                                TaskResult.task_hash == task.content_hash,
                                TaskResult.evaluator_version == "v1",
                            ),
                        ),
                        TaskResult.status == "success",
                        TaskResult.run_id != run.run_id,
                    )
                    .order_by(TaskResult.id.desc())
                    .first()
                )
            result_row.max_retries = max(0, max_retries)
            local.commit()
            if previous_success:
                now = datetime.utcnow()
                result_row.status = "success"
                result_row.response = previous_success.response
                result_row.score = previous_success.score
                result_row.judge_reason = previous_success.judge_reason
                result_row.raw_judge_response = previous_success.raw_judge_response
                result_row.latency = 0.0
                result_row.input_tokens = 0
                result_row.output_tokens = 0
                result_row.error = ""
                result_row.attempt_count = 0
                result_row.max_retries = max(0, max_retries)
                result_row.started_at = now
                result_row.finished_at = now
                local.commit()
                return

            attempts_allowed = max(0, max_retries) + 1
            last_error = ""
            for attempt in range(1, attempts_allowed + 1):
                run_local = local.query(BenchmarkRun).filter(BenchmarkRun.run_id == run.run_id).first()
                local.refresh(result_row)
                if run_local.status == "cancelled" or result_row.status == "cancelled":
                    local.commit()
                    return
                result_row.status = "running"
                result_row.error = ""
                result_row.attempt_count = attempt
                result_row.max_retries = max(0, max_retries)
                result_row.started_at = result_row.started_at or datetime.utcnow()
                result_row.finished_at = None
                local.commit()
                try:
                    try:
                        if task.task_type == "agent_tool" or task.evaluator_type == "agent_trace_eval":
                            agent_output = await asyncio.wait_for(
                                _run_agent_interaction(provider_local, api_key, model_local, task, attempt_timeout),
                                timeout=attempt_timeout,
                            )
                            call_text = agent_output["text"]
                            call_latency = agent_output["latency"]
                            call_input_tokens = agent_output["input_tokens"]
                            call_output_tokens = agent_output["output_tokens"]
                            trace = agent_output["trace"]
                            tool_metrics = agent_output["tool_metrics"]
                        else:
                            call = await asyncio.wait_for(
                                chat_completion(provider_local.api_base, api_key, model_local.model_id, task.prompt, timeout=attempt_timeout),
                                timeout=attempt_timeout,
                            )
                            call_text = call.text or ""
                            call_latency = call.latency or 0.0
                            call_input_tokens = call.input_tokens or 0
                            call_output_tokens = call.output_tokens or 0
                            trace = {}
                            tool_metrics = {}
                    except asyncio.TimeoutError as exc:
                        raise TimeoutError(f"单题模型调用超过 {attempt_timeout:g}s，触发重试") from exc
                    local.refresh(run_local)
                    local.refresh(result_row)
                    if run_local.status == "cancelled" or result_row.status == "cancelled":
                        local.commit()
                        return
                    result_row.response = call_text
                    result_row.latency = (result_row.latency or 0.0) + call_latency
                    result_row.input_tokens = (result_row.input_tokens or 0) + call_input_tokens
                    result_row.output_tokens = (result_row.output_tokens or 0) + call_output_tokens
                    result_row.trace_json = json.dumps(trace, ensure_ascii=False)
                    result_row.tool_metrics_json = json.dumps(tool_metrics, ensure_ascii=False)
                    if not result_row.response.strip() and not trace:
                        raise ValueError("模型返回空回答，无法评估")
                    config = json.loads(task.evaluator_config_json or "{}")
                    if task.evaluator_type in {"agent_trace_eval", "agent_state_machine_eval"}:
                        task_config = json.loads(task.config_json or "{}")
                        config = {
                            **task_config,
                            "evaluation": config,
                            "expected_trace": task_config.get("expected_trace") or {},
                            "agent": task_config.get("agent") or {},
                            "trace": trace,
                        }
                    evaluator = get_evaluator(task.evaluator_type)
                    if isinstance(evaluator, LLMJudgeEvaluator):
                        ev = await evaluator.evaluate_async(call_text, config, _judge)
                    else:
                        ev = evaluator.evaluate(call_text, config)
                    local.refresh(run_local)
                    if run_local.status == "cancelled" or result_row.status == "cancelled":
                        local.commit()
                        return
                    result_row.score = ev.score
                    result_row.judge_reason = ev.reason
                    result_row.raw_judge_response = ev.raw
                    if task.evaluator_type == "agent_state_machine_eval" and ev.raw:
                        try:
                            raw_eval = json.loads(ev.raw)
                            state_summary = raw_eval.get("state_machine") or {}
                            trace["state_machine"] = state_summary
                            tool_metrics["terminal_state"] = state_summary.get("terminal_state")
                            tool_metrics["state_path"] = state_summary.get("path")
                            result_row.trace_json = json.dumps(trace, ensure_ascii=False)
                            result_row.tool_metrics_json = json.dumps(tool_metrics, ensure_ascii=False)
                        except json.JSONDecodeError:
                            pass
                    result_row.status = "success"
                    result_row.error = ""
                    result_row.finished_at = datetime.utcnow()
                    local.commit()
                    return
                except Exception as exc:
                    last_error = str(exc)
                    local.refresh(run_local)
                    local.refresh(result_row)
                    if run_local.status == "cancelled" or result_row.status == "cancelled":
                        local.commit()
                        return
                    if attempt >= attempts_allowed:
                        result_row.status = "failed"
                        result_row.error = last_error
                        result_row.finished_at = datetime.utcnow()
                    else:
                        result_row.status = "pending"
                        result_row.error = f"第 {attempt} 次尝试失败，等待重试：{last_error}"
                    local.commit()
                    if attempt < attempts_allowed:
                        delay = retry_backoff_base * (2 ** (attempt - 1))
                        if "429" in last_error or "Too Many Requests" in last_error:
                            delay = max(delay, retry_backoff_base * 2 * attempt)
                        if delay > 0:
                            await asyncio.sleep(delay)
        finally:
            local.close()

    semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def _bounded(task_id: int):
        async with semaphore:
            await _run_task(task_id)

    try:
        await asyncio.gather(*[_bounded(task.id) for task in tasks])
        session.refresh(run)
        if run.status != "cancelled":
            rows = session.query(TaskResult).filter(TaskResult.run_id == run.run_id).all()
            scores = [row.score for row in rows if row.status == "success" and row.score is not None]
            run.total_score = round(sum(scores) / len(scores), 2) if scores else 0
            run.total_latency = round(sum(row.latency or 0 for row in rows), 3)
            run.status = "completed"
            run.finished_at = datetime.utcnow()
    except Exception as exc:
        session.refresh(run)
        now = datetime.utcnow()
        run.status = "failed"
        run.finished_at = now
        for row in session.query(TaskResult).filter(TaskResult.run_id == run.run_id, TaskResult.status.in_(["pending", "running"])).all():
            row.status = "failed"
            row.error = row.error or f"运行异常中断：{exc}"
            row.finished_at = row.finished_at or now
        raise exc
    finally:
        session.commit()
    return run
