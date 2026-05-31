import time
import httpx
from dataclasses import dataclass


@dataclass
class ModelCallResult:
    text: str
    latency: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict | None = None


async def chat_completion(api_base: str, api_key: str, model_id: str, prompt: str, temperature: float = 0.0, timeout: float = 120.0) -> ModelCallResult:
    url = api_base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=payload)
        resp.raise_for_status()
        data = resp.json()
    latency = round(time.perf_counter() - start, 3)
    usage = data.get("usage") or {}
    text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return ModelCallResult(text=text, latency=latency, input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"), raw=data)
