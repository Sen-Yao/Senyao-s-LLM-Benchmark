from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AppSetting(TimestampMixin, Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Provider(TimestampMixin, Base):
    __tablename__ = "providers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    api_base: Mapped[str] = mapped_column(String(500))
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    api_key_fingerprint: Mapped[str] = mapped_column(String(32), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    models: Mapped[list["LLMModel"]] = relationship(back_populates="provider")


class LLMModel(TimestampMixin, Base):
    __tablename__ = "llm_models"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"))
    display_name: Mapped[str] = mapped_column(String(160), index=True)
    model_id: Mapped[str] = mapped_column(String(240), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    tool_protocol: Mapped[str] = mapped_column(String(40), default="openai_function")
    provider: Mapped[Provider] = relationship(back_populates="models")


class JudgeProfile(TimestampMixin, Base):
    __tablename__ = "judge_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"))
    name: Mapped[str] = mapped_column(String(160), unique=True)
    model_id: Mapped[str] = mapped_column(String(240))
    temperature: Mapped[float] = mapped_column(Float, default=0.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    prompt_template: Mapped[str] = mapped_column(Text, default="")


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(240))
    category: Mapped[str] = mapped_column(String(120), index=True)
    dimension: Mapped[str] = mapped_column(String(120), index=True)
    task_type: Mapped[str] = mapped_column(String(80), default="llm_judged", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    short_description: Mapped[str] = mapped_column(Text, default="")
    prompt: Mapped[str] = mapped_column(Text)
    evaluator_type: Mapped[str] = mapped_column(String(80), index=True)
    evaluator_config_json: Mapped[str] = mapped_column(Text, default="{}")
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    semantic_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    raw_config_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    evaluator_version: Mapped[str] = mapped_column(String(40), default="v1", index=True)
    source_path: Mapped[str] = mapped_column(String(500), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Suite(TimestampMixin, Base):
    __tablename__ = "suites"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")


class SuiteTask(Base):
    __tablename__ = "suite_tasks"
    suite_id: Mapped[int] = mapped_column(ForeignKey("suites.id"), primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)


class BenchmarkRun(TimestampMixin, Base):
    __tablename__ = "benchmark_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("llm_models.id"))
    suite_slug: Mapped[str] = mapped_column(String(120), default="all")
    status: Mapped[str] = mapped_column(String(40), default="pending")
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_latency: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TaskResult(TimestampMixin, Base):
    __tablename__ = "task_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(80), index=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("llm_models.id"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    task_hash: Mapped[str] = mapped_column(String(64), index=True)
    semantic_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    evaluator_version: Mapped[str] = mapped_column(String(40), default="v1", index=True)
    prompt: Mapped[str] = mapped_column(Text)
    response: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    judge_reason: Mapped[str] = mapped_column(Text, default="")
    raw_judge_response: Mapped[str] = mapped_column(Text, default="")
    latency: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    error: Mapped[str] = mapped_column(Text, default="")
    trace_json: Mapped[str] = mapped_column(Text, default="{}")
    tool_metrics_json: Mapped[str] = mapped_column(Text, default="{}")


class TaskChangeEvent(TimestampMixin, Base):
    __tablename__ = "task_change_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_slug: Mapped[str] = mapped_column(String(160), index=True)
    change_type: Mapped[str] = mapped_column(String(40))
    old_hash: Mapped[str] = mapped_column(String(64), default="")
    new_hash: Mapped[str] = mapped_column(String(64), default="")
    requires_rerun: Mapped[bool] = mapped_column(Boolean, default=True)
