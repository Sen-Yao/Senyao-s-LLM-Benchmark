from pydantic import BaseModel, Field


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
