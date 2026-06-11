from pydantic import BaseModel, Field, field_validator


class ProviderIn(BaseModel):
    name: str
    api_base: str
    api_key: str = Field(min_length=1)
    enabled: bool = True
    notes: str = ""


class ProviderPatch(BaseModel):
    name: str | None = None
    api_base: str | None = None
    api_key: str | None = None
    enabled: bool | None = None
    notes: str | None = None


class ProviderOut(BaseModel):
    id: int
    name: str
    api_base: str
    api_key_saved: bool
    api_key_fingerprint: str
    enabled: bool
    notes: str


class ModelIn(BaseModel):
    provider_id: int
    display_name: str
    model_id: str
    enabled: bool = True
    context_window: int | None = None
    input_price: float | None = None
    output_price: float | None = None
    notes: str = ""
    tool_protocol: str = "openai_function"

    @field_validator("tool_protocol")
    @classmethod
    def tool_protocol_must_be_known(cls, value: str) -> str:
        if value not in {"openai_function", "anthropic_tool"}:
            raise ValueError("tool_protocol must be openai_function or anthropic_tool")
        return value


class JudgeProfileIn(BaseModel):
    provider_id: int
    name: str
    model_id: str
    temperature: float = 0.0
    enabled: bool = True
    prompt_template: str = ""


class RunRequest(BaseModel):
    model_ids: list[int]
    suite: str = "all"
    task_slugs: list[str] | None = None
    judge_profile_id: int | None = None
    max_concurrency: int = Field(default=1, ge=1, le=16)
    max_retries: int = Field(default=3, ge=0, le=10)
    force_rerun: bool = False

    @field_validator("model_ids")
    @classmethod
    def model_ids_must_not_be_empty(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("至少选择一个模型")
        return value


class TaskRerunRequest(BaseModel):
    model_ids: list[int] | None = None
    suite: str = "all"
    judge_profile_id: int | None = None
    max_concurrency: int = Field(default=1, ge=1, le=16)
    max_retries: int = Field(default=3, ge=0, le=10)


class ModelPatch(BaseModel):
    provider_id: int | None = None
    display_name: str | None = None
    model_id: str | None = None
    enabled: bool | None = None
    context_window: int | None = None
    input_price: float | None = None
    output_price: float | None = None
    notes: str | None = None
    tool_protocol: str | None = None

    @field_validator("tool_protocol")
    @classmethod
    def tool_protocol_must_be_known(cls, value: str | None) -> str | None:
        if value is not None and value not in {"openai_function", "anthropic_tool"}:
            raise ValueError("tool_protocol must be openai_function or anthropic_tool")
        return value


class JudgeProfilePatch(BaseModel):
    provider_id: int | None = None
    name: str | None = None
    model_id: str | None = None
    temperature: float | None = None
    enabled: bool | None = None
    prompt_template: str | None = None


class ProviderTestRequest(BaseModel):
    model_id: str | None = None


class AppSettingsPatch(BaseModel):
    default_judge_profile_id: int | None = None
