from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from backend.app.adapters.openai_compatible import ToolCall
from backend.app.services.command_policy import analyze_command, command_policy_enabled


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "skill_view": {
        "type": "function",
        "function": {
            "name": "skill_view",
            "description": "Load a fixture-backed skill by name, optionally with a linked file path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "file_path": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    "read_file": {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a fixture-backed file by path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    "search_files": {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search fixture-backed files for a text pattern.",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
    "terminal": {
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "Return fixture-backed command output; no real command is executed.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
}

TOOLSET_TOOLS = {
    "skills": ["skill_view"],
    "file": ["read_file", "search_files"],
    "terminal": ["terminal"],
}


def build_tool_schemas(agent_config: dict[str, Any]) -> list[dict[str, Any]]:
    enabled = agent_config.get("enabled_toolsets") or []
    names: list[str] = []
    for toolset in enabled:
        names.extend(TOOLSET_TOOLS.get(toolset, []))
    names.extend(agent_config.get("enabled_tools") or [])
    return [TOOL_SCHEMAS[name] for name in dict.fromkeys(names) if name in TOOL_SCHEMAS]


def _fixture_entry(fixtures: dict[str, Any], group: str, key: str) -> dict[str, Any] | None:
    entry = (fixtures.get(group) or {}).get(key)
    if isinstance(entry, str):
        return {"content": entry}
    if isinstance(entry, dict):
        return entry
    return None


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    return text[:max_chars] + "\n...[truncated]", True


def execute_fixture_tool(tool_call: ToolCall, task_config: dict[str, Any]) -> dict[str, Any]:
    fixtures = task_config.get("fixtures") or {}
    agent = task_config.get("agent") or {}
    max_chars = int(agent.get("max_tool_result_chars", 8000))
    name = tool_call.name
    args = tool_call.arguments or {}

    if name == "skill_view":
        skill_name = args.get("name", "")
        entry = _fixture_entry(fixtures, "skills", skill_name)
        if not entry:
            return _error(tool_call, "fixture_not_found", f"skill fixture not found: {skill_name}")
        content, truncated = _truncate_text(str(entry.get("content", "")), max_chars)
        observation = {
            "summary": f"Loaded skill fixture {skill_name}",
            "name": skill_name,
            "content": content,
            "linked_files": entry.get("linked_files", {}),
            "truncated": truncated,
        }
        return _success(tool_call, observation, raw_chars=len(str(entry.get("content", ""))))

    if name == "read_file":
        path = args.get("path", "")
        entry = _fixture_entry(fixtures, "files", path)
        if not entry:
            return _error(tool_call, "fixture_not_found", f"file fixture not found: {path}")
        lines = str(entry.get("content", "")).splitlines()
        offset = max(1, int(args.get("offset", 1)))
        limit = int(args.get("limit", 120))
        selected = "\n".join(lines[offset - 1 : offset - 1 + limit])
        content, truncated_chars = _truncate_text(selected, max_chars)
        truncated = truncated_chars or offset - 1 + limit < len(lines)
        observation = {"path": path, "range": f"lines {offset}-{offset + limit - 1}", "content": content, "total_lines": len(lines), "truncated": truncated}
        return _success(tool_call, observation, raw_chars=len(str(entry.get("content", ""))))

    if name == "search_files":
        pattern = str(args.get("pattern", ""))
        matches = []
        for path, entry in (fixtures.get("files") or {}).items():
            content = entry.get("content", "") if isinstance(entry, dict) else str(entry)
            for idx, line in enumerate(str(content).splitlines(), start=1):
                if pattern and pattern in line:
                    matches.append({"path": path, "line": idx, "preview": line[:200]})
        observation = {"matches": matches[:10], "total_matches": len(matches), "truncated": len(matches) > 10}
        return _success(tool_call, observation, raw_chars=len(json.dumps(matches, ensure_ascii=False)))

    if name == "terminal":
        command = args.get("command", "")
        if command_policy_enabled(task_config):
            analysis = analyze_command(command, task_config)
            if analysis["status"] in {"dangerous", "invalid"}:
                status = "denied" if analysis["status"] == "dangerous" else "error"
                return _result(
                    tool_call,
                    status,
                    {
                        "command": command,
                        "error_type": f"command_{analysis['status']}",
                        "error_message": f"command policy rejected terminal command: {analysis['status']}",
                        "command_policy": analysis,
                        "truncated": False,
                    },
                    raw_chars=len(command),
                )
        entry = _fixture_entry(fixtures, "terminal", command)
        if not entry:
            return _error(tool_call, "fixture_not_found", f"terminal fixture not found: {command}")
        output, truncated = _truncate_text(str(entry.get("stdout", "")), max_chars)
        observation = {"command": command, "exit_code": int(entry.get("exit_code", 0)), "important_output": output.splitlines()[-20:], "truncated": truncated}
        if command_policy_enabled(task_config):
            observation["command_policy"] = analyze_command(command, task_config)
        status = "success" if observation["exit_code"] == 0 else "error"
        return _result(tool_call, status, observation, raw_chars=len(str(entry.get("stdout", ""))))

    return _error(tool_call, "unknown_tool", f"unknown fixture tool: {name}")


def _success(tool_call: ToolCall, observation: dict[str, Any], raw_chars: int = 0) -> dict[str, Any]:
    return _result(tool_call, "success", observation, raw_chars=raw_chars)


def _error(tool_call: ToolCall, error_type: str, message: str) -> dict[str, Any]:
    return _result(tool_call, "error", {"error_type": error_type, "error_message": message, "truncated": False}, raw_chars=len(message))


def _result(tool_call: ToolCall, status: str, observation: dict[str, Any], raw_chars: int = 0) -> dict[str, Any]:
    returned = json.dumps(observation, ensure_ascii=False)
    return {
        "call_id": tool_call.id,
        "tool_name": tool_call.name,
        "arguments": tool_call.arguments,
        "status": status,
        "observation": observation,
        "usage": {"returned_chars": len(returned), "raw_chars": raw_chars or len(returned)},
        "raw_tool_call": asdict(tool_call),
    }
