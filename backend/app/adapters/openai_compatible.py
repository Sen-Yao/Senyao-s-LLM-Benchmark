from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


class ApiResponseError(RuntimeError):
    pass


def parse_json_response(url: str, status_code: int, content_type: str, text: str) -> dict:
    if not text.strip():
        raise ApiResponseError(f"{url} 返回空响应（HTTP {status_code}），无法解析为 JSON")
    if "json" not in (content_type or "").lower():
        preview = text.strip().replace("\n", " ")[:160]
        raise ApiResponseError(f"{url} 返回非 JSON 响应（HTTP {status_code}, Content-Type: {content_type or 'unknown'}）：{preview}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        preview = text.strip().replace("\n", " ")[:160]
        raise ApiResponseError(f"{url} JSON 解析失败（HTTP {status_code}）：{exc.msg}；响应片段：{preview}") from exc
    if not isinstance(data, dict):
        raise ApiResponseError(f"{url} 返回 JSON 不是对象：{type(data).__name__}")
    return data


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] | None = None


@dataclass
class ModelCallResult:
    text: str
    latency: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


def _extract_tool_calls(message: dict[str, Any]) -> list[ToolCall]:
    calls = []
    for idx, raw_call in enumerate(message.get("tool_calls") or []):
        function = raw_call.get("function") or {}
        name = function.get("name") or raw_call.get("name") or ""
        arguments_raw = function.get("arguments", raw_call.get("arguments", {}))
        if isinstance(arguments_raw, str):
            try:
                arguments = json.loads(arguments_raw or "{}")
            except json.JSONDecodeError:
                arguments = {"_raw": arguments_raw}
        elif isinstance(arguments_raw, dict):
            arguments = arguments_raw
        else:
            arguments = {}
        calls.append(
            ToolCall(
                id=raw_call.get("id") or f"tool_call_{idx + 1}",
                name=name,
                arguments=arguments,
                raw=raw_call,
            )
        )
    return calls


def _parse_chat_completion(data: dict[str, Any], latency: float) -> ModelCallResult:
    usage = data.get("usage") or {}
    message = data.get("choices", [{}])[0].get("message", {}) or {}
    content = message.get("content") or ""
    return ModelCallResult(
        text=content,
        latency=latency,
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
        raw=data,
        tool_calls=_extract_tool_calls(message),
    )


def _convert_tool_schema(tool: dict[str, Any]) -> dict[str, Any]:
    if tool.get("type") != "function":
        return tool
    function = tool.get("function") or {}
    return {
        "type": "custom",
        "name": function.get("name", ""),
        "description": function.get("description", ""),
        "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
    }


def normalize_tools_for_protocol(tools: list[dict[str, Any]] | None, tool_protocol: str = "openai_function") -> list[dict[str, Any]] | None:
    if not tools:
        return tools
    if tool_protocol == "anthropic_tool":
        return [_convert_tool_schema(tool) for tool in tools]
    return tools


async def chat_completion(
    api_base: str,
    api_key: str,
    model_id: str,
    prompt: str,
    temperature: float = 0.0,
    timeout: float = 120.0,
    max_tokens: int | None = None,
) -> ModelCallResult:
    return await chat_completion_messages(
        api_base=api_base,
        api_key=api_key,
        model_id=model_id,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        timeout=timeout,
        max_tokens=max_tokens,
    )


async def chat_completion_messages(
    api_base: str,
    api_key: str,
    model_id: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.0,
    timeout: float = 120.0,
    max_tokens: int | None = None,
    tool_protocol: str = "openai_function",
) -> ModelCallResult:
    url = api_base.rstrip("/") + "/chat/completions"
    payload: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = normalize_tools_for_protocol(tools, tool_protocol)
    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()
        data = parse_json_response(url, resp.status_code, resp.headers.get("content-type", ""), resp.text)
    latency = round(time.perf_counter() - start, 3)
    return _parse_chat_completion(data, latency)


async def list_models(api_base: str, api_key: str, timeout: float = 30.0) -> list[str]:
    """Fetch model ids from an OpenAI-compatible /models endpoint."""
    url = api_base.rstrip("/") + "/models"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
        resp.raise_for_status()
        data = parse_json_response(url, resp.status_code, resp.headers.get("content-type", ""), resp.text)
    rows = data.get("data", []) if isinstance(data, dict) else []
    ids = [row.get("id") for row in rows if isinstance(row, dict) and row.get("id")]
    return sorted(set(ids))
