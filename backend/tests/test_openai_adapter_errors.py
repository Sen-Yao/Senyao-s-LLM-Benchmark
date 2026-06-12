import pytest

from backend.app.adapters.openai_compatible import ApiResponseError, normalize_tools_for_protocol, parse_json_response


def test_parse_json_response_raises_useful_error_for_html_body():
    with pytest.raises(ApiResponseError) as exc:
        parse_json_response("http://example.test/models", 200, "text/html; charset=utf-8", "<!doctype html><title>UI</title>")

    message = str(exc.value)
    assert "非 JSON 响应" in message
    assert "text/html" in message
    assert "http://example.test/models" in message
    assert "Expecting value" not in message


def test_parse_json_response_raises_useful_error_for_empty_body():
    with pytest.raises(ApiResponseError) as exc:
        parse_json_response("http://example.test/models", 200, "application/json", "")

    assert "空响应" in str(exc.value)
    assert "Expecting value" not in str(exc.value)


def test_normalize_tools_for_protocol_keeps_openai_function_tools_by_default():
    tools = [{"type": "function", "function": {"name": "skill_view", "parameters": {"type": "object"}}}]

    assert normalize_tools_for_protocol(tools) == tools


def test_normalize_tools_for_protocol_converts_function_tools_for_anthropic():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "skill_view",
                "description": "Load a skill",
                "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            },
        }
    ]

    converted = normalize_tools_for_protocol(tools, "anthropic_tool")

    assert converted == [
        {
            "type": "custom",
            "name": "skill_view",
            "description": "Load a skill",
            "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        }
    ]
