import json
import uuid
from datetime import datetime
from sqlalchemy.orm import Session
from backend.app.adapters.openai_compatible import chat_completion
from backend.app.evaluators.llm_judge import LLMJudgeEvaluator
from backend.app.evaluators.registry import get_evaluator
from backend.app.models import BenchmarkRun, JudgeProfile, LLMModel, Provider, Task, TaskResult
from backend.app.security.crypto import decrypt_secret


async def run_model_tasks(session: Session, model_id: int, task_slugs: list[str] | None = None, judge_profile_id: int | None = None, suite: str = "all") -> BenchmarkRun:
    model = session.get(LLMModel, model_id)
    if not model:
        raise ValueError("model not found")
    provider = session.get(Provider, model.provider_id)
    api_key = decrypt_secret(provider.encrypted_api_key)
    query = session.query(Task).filter(Task.active.is_(True))
    if task_slugs:
        query = query.filter(Task.slug.in_(task_slugs))
    tasks = query.order_by(Task.category, Task.slug).all()
    run = BenchmarkRun(run_id=uuid.uuid4().hex[:16], model_id=model.id, suite_slug=suite, status="running", started_at=datetime.utcnow())
    session.add(run)
    session.commit()

    judge_callable = None
    if judge_profile_id:
        judge_profile = session.get(JudgeProfile, judge_profile_id)
        judge_provider = session.get(Provider, judge_profile.provider_id) if judge_profile else None
        if judge_profile and judge_provider:
            judge_key = decrypt_secret(judge_provider.encrypted_api_key)
            async def _judge(prompt: str) -> str:
                result = await chat_completion(judge_provider.api_base, judge_key, judge_profile.model_id, prompt, temperature=judge_profile.temperature)
                return result.text
            judge_callable = _judge

    scores = []
    total_latency = 0.0
    try:
        for task in tasks:
            result_row = TaskResult(run_id=run.run_id, model_id=model.id, task_id=task.id, task_hash=task.content_hash, prompt=task.prompt, status="running")
            session.add(result_row)
            session.commit()
            try:
                call = await chat_completion(provider.api_base, api_key, model.model_id, task.prompt)
                result_row.response = call.text
                result_row.latency = call.latency
                result_row.input_tokens = call.input_tokens
                result_row.output_tokens = call.output_tokens
                total_latency += call.latency
                config = json.loads(task.evaluator_config_json or "{}")
                evaluator = get_evaluator(task.evaluator_type)
                if isinstance(evaluator, LLMJudgeEvaluator):
                    ev = await evaluator.evaluate_async(call.text, config, judge_callable)
                else:
                    ev = evaluator.evaluate(call.text, config)
                result_row.score = ev.score
                result_row.judge_reason = ev.reason
                result_row.raw_judge_response = ev.raw
                result_row.status = "success"
                scores.append(ev.score)
            except Exception as exc:
                result_row.status = "failed"
                result_row.error = str(exc)
            session.commit()
        run.total_score = round(sum(scores)/len(scores), 2) if scores else 0
        run.total_latency = round(total_latency, 3)
        run.status = "completed"
        run.finished_at = datetime.utcnow()
    except Exception as exc:
        run.status = "failed"
        run.finished_at = datetime.utcnow()
        raise exc
    finally:
        session.commit()
    return run
