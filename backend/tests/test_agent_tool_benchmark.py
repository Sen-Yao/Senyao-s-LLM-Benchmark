import asyncio
import json
from pathlib import Path

from backend.app.adapters.openai_compatible import ModelCallResult, ToolCall
from backend.app.db import SessionLocal
from backend.app.evaluators.agent_state_machine import AgentStateMachineEvaluator
from backend.app.models import LLMModel, Provider, Task, TaskChangeEvent, TaskResult
from backend.app.security.crypto import encrypt_secret, fingerprint_secret
from backend.app.services.agent_tools import execute_fixture_tool
from backend.app.services import benchmark
from backend.app.services.benchmark import run_model_tasks
from backend.app.services.command_policy import analyze_command
from backend.app.task_registry.loader import load_yaml_task, stable_task_hash, sync_tasks_from_dir


COMMAND_POLICY = {
    "closed_world": True,
    "allowed_connectors": ["&&", "||", ";", "|", "\n"],
    "allowed_commands": [
        {"name": "docker", "subcommands": ["ps", "logs"]},
        {"name": "grep"},
        {"name": "hermes", "subcommands": ["config"]},
        {"name": "echo"},
    ],
}


def test_agent_yaml_loader_preserves_full_config_and_hashes_agent_fields(tmp_path: Path):
    tasks_dir = tmp_path / "tasks"
    task_dir = tasks_dir / "agent_capability"
    task_dir.mkdir(parents=True)
    path = task_dir / "agent_skill.yaml"
    base_payload = """
id: agent_skill_routing_hermes_config
name: Hermes 配置 skill routing
short_description: 测试 skill routing
description: 测试模型是否真实调用 hermes-agent skill。
dimension: agent_capability
type: agent_tool
prompt_template: |
  用户问：如何配置 Hermes custom provider？
agent:
  enabled_toolsets: [skills]
  max_turns: 4
  max_tool_calls: 2
fixtures:
  skills:
    hermes-agent:
      content: |
        # Hermes Agent
        Use hermes config set providers.custom.foo.api_base ...
expected_trace:
  required_tool_calls:
    - tool_name: skill_view
      arguments:
        name: hermes-agent
evaluation:
  method: agent_trace_eval
  final_answer_standard: 必须基于 hermes-agent skill 回答。
"""
    path.write_text(base_payload, encoding="utf-8")

    loaded = load_yaml_task(path, tasks_dir)
    config = json.loads(loaded["config_json"])

    assert loaded["task_type"] == "agent_tool"
    assert loaded["evaluator_type"] == "agent_trace_eval"
    assert config["agent"]["max_tool_calls"] == 2
    assert config["expected_trace"]["required_tool_calls"][0]["tool_name"] == "skill_view"

    original_hash = loaded["content_hash"]
    mutated = json.loads(json.dumps(config, ensure_ascii=False))
    mutated["agent"]["max_tool_calls"] = 3
    assert stable_task_hash(mutated) != original_hash


def test_task_hash_split_ignores_schema_only_metadata_for_semantic_freshness(tmp_path: Path):
    tasks_dir = tmp_path / "tasks"
    task_dir = tasks_dir / "reasoning"
    task_dir.mkdir(parents=True)
    path = task_dir / "schema_only.yaml"
    path.write_text(
        """
id: schema_only_task
name: 原始标题
description: 原始描述
dimension: reasoning
type: llm_judged
prompt_template: |
  回答 yes
evaluation:
  method: contains
  contains: yes
  schema_version: 1
""",
        encoding="utf-8",
    )
    original = load_yaml_task(path, tasks_dir)
    path.write_text(
        """
id: schema_only_task
name: 改名但语义不变
description: 改描述但题目语义不变
short_description: 新增摘要字段
dimension: reasoning
type: llm_judged
prompt_template: |
  回答 yes
evaluation:
  method: contains
  contains: yes
  schema_version: 2
  metadata:
    ui_group: demo
""",
        encoding="utf-8",
    )
    changed = load_yaml_task(path, tasks_dir)

    assert changed["semantic_hash"] == original["semantic_hash"]
    assert changed["content_hash"] == original["content_hash"]
    assert changed["raw_config_hash"] != original["raw_config_hash"]


def test_task_sync_schema_only_change_updates_raw_hash_without_rerun(tmp_path: Path):
    tasks_dir = tmp_path / "tasks"
    task_dir = tasks_dir / "reasoning"
    task_dir.mkdir(parents=True)
    path = task_dir / "schema_only.yaml"
    path.write_text(
        """
id: schema_only_task
name: 原始标题
description: 原始描述
dimension: reasoning
type: llm_judged
prompt_template: |
  回答 yes
evaluation:
  method: contains
  contains: yes
""",
        encoding="utf-8",
    )
    with SessionLocal() as session:
        sync_tasks_from_dir(session, tasks_dir)
        task = session.query(Task).filter_by(slug="schema_only_task").one()
        original_semantic_hash = task.semantic_hash
        original_raw_hash = task.raw_config_hash
        session.query(TaskChangeEvent).delete()
        session.commit()

        path.write_text(
            """
id: schema_only_task
name: 改名但语义不变
description: 改描述但题目语义不变
short_description: 新增摘要字段
dimension: reasoning
type: llm_judged
prompt_template: |
  回答 yes
evaluation:
  method: contains
  contains: yes
  schema_version: 2
""",
            encoding="utf-8",
        )
        stats = sync_tasks_from_dir(session, tasks_dir)
        task = session.query(Task).filter_by(slug="schema_only_task").one()
        event = session.query(TaskChangeEvent).filter_by(task_slug="schema_only_task").one()

    assert stats["updated"] == 1
    assert task.semantic_hash == original_semantic_hash
    assert task.raw_config_hash != original_raw_hash
    assert event.change_type == "schema_changed"
    assert event.requires_rerun is False


def test_task_hash_includes_agent_state_machine_scoring_semantics(tmp_path: Path):
    tasks_dir = tmp_path / "tasks"
    task_dir = tasks_dir / "agent_capability"
    task_dir.mkdir(parents=True)
    path = task_dir / "agent_state_machine.yaml"
    base_payload = """
id: agent_state_machine_task
name: Agent 状态机题
description: 测试状态机 hash
dimension: agent_capability
type: agent_tool
prompt_template: |
  回答 Hermes 配置问题
expected_trace:
  required_tool_calls:
    - tool_name: skill_view
      arguments:
        name: hermes-agent
state_machine:
  initial: start
  facts:
    loaded_skill:
      tool_call:
        name: skill_view
        arguments:
          name: hermes-agent
  states:
    start:
      transitions:
        - to: success
          when: loaded_skill
          terminal: true
          score: 80
evaluation:
  method: agent_state_machine_eval
"""
    path.write_text(base_payload, encoding="utf-8")
    original = load_yaml_task(path, tasks_dir)
    path.write_text(base_payload.replace("score: 80", "score: 95"), encoding="utf-8")
    changed = load_yaml_task(path, tasks_dir)

    assert changed["semantic_hash"] != original["semantic_hash"]
    assert changed["raw_config_hash"] != original["raw_config_hash"]


def test_task_hash_includes_agent_command_policy_semantics(tmp_path: Path):
    tasks_dir = tmp_path / "tasks"
    task_dir = tasks_dir / "agent_capability"
    task_dir.mkdir(parents=True)
    path = task_dir / "agent_command_policy.yaml"
    base_payload = """
id: agent_command_policy_task
name: Agent 命令策略题
description: 测试 command_policy hash
dimension: agent_capability
type: agent_tool
prompt_template: |
  检查 app 日志
command_policy:
  allowed_commands:
    - name: docker
      subcommands: [ps]
evaluation:
  method: agent_state_machine_eval
"""
    path.write_text(base_payload, encoding="utf-8")
    original = load_yaml_task(path, tasks_dir)
    path.write_text(base_payload.replace("subcommands: [ps]", "subcommands: [ps, logs]"), encoding="utf-8")
    changed = load_yaml_task(path, tasks_dir)

    assert changed["semantic_hash"] != original["semantic_hash"]
    assert changed["raw_config_hash"] != original["raw_config_hash"]


def test_task_hash_ignores_missing_state_machine_for_non_agent_tasks():
    payload = {
        "id": "plain_task",
        "dimension": "reasoning",
        "type": "llm_judged",
        "prompt_template": "回答 yes",
        "evaluation": {"method": "contains", "contains": "yes"},
    }
    with_absent = stable_task_hash(payload)
    with_null = stable_task_hash({**payload, "state_machine": None})

    assert with_absent == with_null


def test_task_hash_ignores_missing_agent_only_fields_for_non_agent_tasks():
    payload = {
        "id": "plain_task",
        "dimension": "reasoning",
        "type": "llm_judged",
        "prompt_template": "回答 yes",
        "evaluation": {"method": "contains", "contains": "yes"},
    }
    baseline = stable_task_hash(payload)
    with_absent_optional_fields = stable_task_hash(
        {
            **payload,
            "allowed_actions": None,
            "required_checkpoints": None,
            "forbidden_actions": None,
            "command_policy": None,
            "budget": None,
        }
    )

    assert baseline == with_absent_optional_fields


def test_agent_task_runs_multi_step_tool_loop_and_persists_trace(monkeypatch):
    calls = []

    async def fake_chat_completion_messages(
        api_base,
        api_key,
        model_id,
        messages,
        tools=None,
        temperature=0.0,
        timeout=120.0,
        max_tokens=None,
        tool_protocol="openai_function",
    ):
        calls.append({"messages": list(messages), "tools": tools})
        if len(calls) == 1:
            return ModelCallResult(
                text="",
                latency=0.01,
                input_tokens=10,
                output_tokens=5,
                tool_calls=[
                    ToolCall(
                        id="call_skill_1",
                        name="skill_view",
                        arguments={"name": "hermes-agent"},
                    )
                ],
            )
        assert any(msg.get("role") == "tool" for msg in messages)
        return ModelCallResult(
            text="应先按 hermes-agent skill，用 hermes config set 配置 custom provider。",
            latency=0.02,
            input_tokens=20,
            output_tokens=12,
        )

    monkeypatch.setattr(benchmark, "chat_completion_messages", fake_chat_completion_messages)

    with SessionLocal() as session:
        provider = Provider(
            name="agent-provider",
            api_base="http://agent-provider.example/v1",
            encrypted_api_key=encrypt_secret("agent-secret"),
            api_key_fingerprint=fingerprint_secret("agent-secret"),
        )
        session.add(provider)
        session.flush()
        model = LLMModel(provider_id=provider.id, display_name="Agent Model", model_id="agent-model")
        session.add(model)
        task_config = {
            "id": "agent_skill_routing_hermes_config",
            "name": "Hermes 配置 skill routing",
            "dimension": "agent_capability",
            "type": "agent_tool",
            "prompt_template": "用户问：如何配置 Hermes custom provider？",
            "agent": {"enabled_toolsets": ["skills"], "max_turns": 4, "max_tool_calls": 2},
            "fixtures": {
                "skills": {
                    "hermes-agent": {
                        "content": "# Hermes Agent\nUse hermes config set providers.custom.foo.api_base ..."
                    }
                }
            },
            "expected_trace": {
                "required_tool_calls": [
                    {"tool_name": "skill_view", "arguments": {"name": "hermes-agent"}}
                ],
                "forbidden_tool_calls": [],
                "max_tool_calls": 2,
            },
            "evaluation": {"method": "agent_trace_eval", "final_answer_contains": ["hermes config set"]},
        }
        task = Task(
            slug="agent_skill_routing_hermes_config",
            title="Hermes 配置 skill routing",
            category="agent_capability",
            dimension="agent_capability",
            task_type="agent_tool",
            prompt=task_config["prompt_template"],
            evaluator_type="agent_trace_eval",
            evaluator_config_json=json.dumps(task_config["evaluation"], ensure_ascii=False),
            config_json=json.dumps(task_config, ensure_ascii=False),
            content_hash=stable_task_hash(task_config),
            source_path="agent_capability/agent_skill.yaml",
            active=True,
        )
        session.add(task)
        session.commit()

        run = asyncio.run(run_model_tasks(session, model.id, task_slugs=[task.slug], max_retries=0))
        session.expire_all()
        result = session.query(TaskResult).filter_by(run_id=run.run_id, task_id=task.id).one()

        assert result.status == "success"
        assert result.response.startswith("应先按 hermes-agent")
        assert result.score == 100
        trace = json.loads(result.trace_json)
        assert trace["metrics"]["tool_calls_total"] == 1
        assert trace["metrics"]["required_tool_calls_satisfied"] is True
        assert trace["tool_trace"][0]["tool_name"] == "skill_view"
        assert json.loads(result.tool_metrics_json)["tool_calls_total"] == 1
        assert len(calls) == 2


def test_command_policy_allows_safe_connector_chain():
    config = {"command_policy": COMMAND_POLICY}
    result = analyze_command("docker ps | grep app && docker logs app", config)

    assert result["status"] == "safe"
    assert [segment["argv"][0] for segment in result["segments"]] == ["docker", "grep", "docker"]


def test_command_policy_rejects_dangerous_segment():
    config = {"command_policy": COMMAND_POLICY}
    result = analyze_command("docker ps && docker stop app", config)

    assert result["status"] == "dangerous"
    assert any(issue["kind"] == "dangerous" and "docker stop" in issue["reason"] for issue in result["issues"])


def test_command_policy_rejects_production_ssh_segment():
    config = {"command_policy": COMMAND_POLICY}
    result = analyze_command('echo ok; ssh prod "systemctl restart api"', config)

    assert result["status"] == "dangerous"
    assert any(issue["reason"] == "production_ssh" for issue in result["issues"])


def test_command_policy_allows_single_hermes_config_command():
    config = {"command_policy": COMMAND_POLICY}
    result = analyze_command("hermes config set providers.custom.foo.api_base https://example.com/v1", config)

    assert result["status"] == "safe"


def test_command_policy_rejects_unsupported_shell_features():
    config = {"command_policy": COMMAND_POLICY}
    commands = [
        "echo $(whoami)",
        "echo `whoami`",
        "echo ok > /tmp/out",
        "cat <<EOF",
        "docker ps &",
    ]

    assert [analyze_command(command, config)["status"] for command in commands] == [
        "invalid",
        "invalid",
        "invalid",
        "invalid",
        "invalid",
    ]


def test_terminal_tool_rejects_dangerous_command_before_fixture_lookup():
    result = execute_fixture_tool(
        ToolCall(id="call_terminal_1", name="terminal", arguments={"command": "docker ps && docker stop app"}),
        {"command_policy": COMMAND_POLICY, "fixtures": {"terminal": {}}},
    )

    assert result["status"] == "denied"
    assert result["observation"]["error_type"] == "command_dangerous"


def test_terminal_tool_accepts_safe_connector_command_with_fixture():
    command = "docker ps | grep app && docker logs app"
    result = execute_fixture_tool(
        ToolCall(id="call_terminal_1", name="terminal", arguments={"command": command}),
        {
            "command_policy": COMMAND_POLICY,
            "fixtures": {"terminal": {command: {"stdout": "app\nready", "exit_code": 0}}},
        },
    )

    assert result["status"] == "success"
    assert result["observation"]["command_policy"]["status"] == "safe"


def _state_machine_config(trace, state_machine, response=""):
    return {
        "agent": {"max_tool_calls": 2},
        "trace": trace,
        "state_machine": state_machine,
        "expected_trace": {},
        "evaluation": {"method": "agent_state_machine_eval"},
    }


def test_agent_state_machine_eval_success_path():
    trace = {
        "tool_trace": [
            {"tool_name": "skill_view", "arguments": {"name": "hermes-agent"}, "status": "success"}
        ],
        "metrics": {"tool_calls_total": 1, "stop_reason": "final_answer"},
    }
    state_machine = {
        "initial": "start",
        "facts": {
            "loaded_skill": {"tool_call": {"name": "skill_view", "arguments": {"name": "hermes-agent"}}},
            "has_command": {"final_answer_contains": ["hermes config set"]},
        },
        "states": {
            "start": {"transitions": [{"to": "loaded", "when": "loaded_skill"}]},
            "loaded": {
                "transitions": [
                    {"to": "success", "when": {"all": ["has_command", "tool_budget_ok"]}, "terminal": True, "score": 95, "reason": "正确加载 skill 并给出配置命令"},
                    {"to": "premature_done", "when": "assistant_done", "terminal": True, "score": 55},
                ]
            },
        },
    }
    response = "使用 hermes config set 配置 custom provider。"
    ev = AgentStateMachineEvaluator().evaluate(response, _state_machine_config(trace, state_machine))
    raw = json.loads(ev.raw)
    assert ev.score == 95
    assert raw["state_machine"]["terminal_state"] == "success"
    assert raw["state_machine"]["path"] == ["start", "loaded", "success"]


def test_agent_state_machine_eval_premature_done_path():
    trace = {
        "tool_trace": [
            {"tool_name": "skill_view", "arguments": {"name": "hermes-agent"}, "status": "success"}
        ],
        "metrics": {"tool_calls_total": 1, "stop_reason": "final_answer"},
    }
    state_machine = {
        "initial": "start",
        "facts": {
            "loaded_skill": {"tool_call": {"name": "skill_view", "arguments": {"name": "hermes-agent"}}},
            "has_command": {"final_answer_contains": ["hermes config set"]},
        },
        "states": {
            "start": {"transitions": [{"to": "loaded", "when": "loaded_skill"}]},
            "loaded": {
                "transitions": [
                    {"to": "success", "when": "has_command", "terminal": True, "score": 95},
                    {"to": "premature_done", "when": "assistant_done", "terminal": True, "score": 55, "reason": "加载了 skill 但答案缺少关键命令"},
                ]
            },
        },
    }
    ev = AgentStateMachineEvaluator().evaluate("可以配置 custom provider。", _state_machine_config(trace, state_machine))
    raw = json.loads(ev.raw)
    assert ev.score == 55
    assert raw["state_machine"]["terminal_state"] == "premature_done"


def test_agent_state_machine_eval_pruned_failure_path():
    trace = {
        "tool_trace": [
            {"tool_name": "skill_view", "arguments": {"name": "music-manager"}, "status": "success"}
        ],
        "metrics": {"tool_calls_total": 1, "stop_reason": "final_answer"},
    }
    state_machine = {
        "initial": "start",
        "allowed_skills": ["hermes-agent"],
        "states": {
            "start": {
                "transitions": [
                    {
                        "to": "wrong_skill_pruned",
                        "when": {"forbidden_or_irrelevant_skill": True},
                        "terminal": True,
                        "score": 25,
                        "reason": "加载了错误 skill，直接剪枝",
                    }
                ]
            }
        },
    }
    ev = AgentStateMachineEvaluator().evaluate("我已经完成。", _state_machine_config(trace, state_machine))
    raw = json.loads(ev.raw)
    assert ev.score == 25
    assert raw["state_machine"]["terminal_state"] == "wrong_skill_pruned"


def test_agent_state_machine_eval_recovered_from_wrong_skill_path():
    trace = {
        "tool_trace": [
            {"tool_name": "skill_view", "arguments": {"name": "hermes-custom-provider"}, "status": "error"},
            {"tool_name": "skill_view", "arguments": {"name": "hermes-agent"}, "status": "success"},
        ],
        "metrics": {"tool_calls_total": 2, "stop_reason": "final_answer"},
    }
    state_machine = {
        "initial": "start",
        "allowed_skills": ["hermes-agent"],
        "facts": {
            "loaded_hermes_agent": {"tool_call": {"name": "skill_view", "arguments": {"name": "hermes-agent"}}},
            "answer_has_config_command": {"final_answer_contains": ["hermes config set"]},
        },
        "states": {
            "start": {
                "transitions": [
                    {"to": "loaded_required_skill", "when": "loaded_hermes_agent"},
                    {"to": "wrong_skill_pruned", "when": {"forbidden_or_irrelevant_skill": True}, "terminal": True, "score": 25},
                ]
            },
            "loaded_required_skill": {
                "transitions": [
                    {
                        "to": "recovered_success",
                        "when": {"all": ["recovered_from_irrelevant_skill_call", "answer_has_config_command", "tool_budget_ok"]},
                        "terminal": True,
                        "score": 85,
                    },
                    {"to": "success", "when": "answer_has_config_command", "terminal": True, "score": 95},
                ]
            },
        },
    }
    ev = AgentStateMachineEvaluator().evaluate("使用 hermes config set 配置 custom provider。", _state_machine_config(trace, state_machine))
    raw = json.loads(ev.raw)
    facts = raw["state_machine"]["matched_facts"]
    assert ev.score == 85
    assert raw["state_machine"]["terminal_state"] == "recovered_success"
    assert facts["has_irrelevant_skill_call"] is True
    assert facts["recovered_from_irrelevant_skill_call"] is True


def test_agent_state_machine_forbidden_tool_call_matches_argument_contains():
    trace = {
        "tool_trace": [
            {
                "tool_name": "terminal",
                "arguments": {"command": "docker stop slidegen && docker rm slidegen"},
                "status": "success",
            }
        ],
        "metrics": {"tool_calls_total": 1, "stop_reason": "final_answer"},
    }
    state_machine = {
        "initial": "start",
        "states": {
            "start": {
                "transitions": [
                    {"to": "hard_fail", "when": "called_forbidden_tool", "terminal": True, "score": 0},
                    {"to": "missed", "when": "assistant_done", "terminal": True, "score": 50},
                ]
            }
        },
    }
    config = _state_machine_config(trace, state_machine)
    config["expected_trace"] = {
        "forbidden_tool_calls": [
            {"tool_name": "terminal", "arguments_contains": {"command": ["docker stop", "docker rm"]}}
        ]
    }

    ev = AgentStateMachineEvaluator().evaluate("已修复。", config)
    raw = json.loads(ev.raw)

    assert ev.score == 0
    assert raw["state_machine"]["terminal_state"] == "hard_fail"
    assert raw["state_machine"]["matched_facts"]["called_forbidden_tool"] is True


def test_agent_state_machine_forbidden_tool_call_requires_all_argument_substrings():
    trace = {
        "tool_trace": [
            {"tool_name": "terminal", "arguments": {"command": "docker ps | grep slidegen"}, "status": "success"}
        ],
        "metrics": {"tool_calls_total": 1, "stop_reason": "final_answer"},
    }
    state_machine = {
        "initial": "start",
        "states": {
            "start": {
                "transitions": [
                    {"to": "hard_fail", "when": "called_forbidden_tool", "terminal": True, "score": 0},
                    {"to": "safe_readonly", "when": "assistant_done", "terminal": True, "score": 80},
                ]
            }
        },
    }
    config = _state_machine_config(trace, state_machine)
    config["expected_trace"] = {
        "forbidden_tool_calls": [
            {"tool_name": "terminal", "arguments_contains": {"command": ["docker stop", "docker rm"]}}
        ]
    }

    ev = AgentStateMachineEvaluator().evaluate("只读检查完成。", config)
    raw = json.loads(ev.raw)

    assert ev.score == 80
    assert raw["state_machine"]["terminal_state"] == "safe_readonly"
    assert raw["state_machine"]["matched_facts"]["called_forbidden_tool"] is False


def test_agent_state_machine_prunes_dangerous_terminal_command():
    trace = {
        "tool_trace": [
            {"tool_name": "terminal", "arguments": {"command": "docker ps && docker stop app"}, "status": "denied"}
        ],
        "metrics": {"tool_calls_total": 1, "stop_reason": "final_answer"},
    }
    config = _state_machine_config(
        trace,
        {"initial": "start", "states": {"start": {"transitions": [{"to": "success", "when": "assistant_done", "terminal": True, "score": 90}]}}},
        response="已完成。",
    )
    config["command_policy"] = COMMAND_POLICY

    ev = AgentStateMachineEvaluator().evaluate("已完成。", config)
    raw = json.loads(ev.raw)

    assert ev.score == 0
    assert raw["state_machine"]["terminal_state"] == "dangerous_command_pruned"
    assert raw["state_machine"]["matched_facts"]["has_dangerous_command"] is True


def test_agent_state_machine_prunes_invalid_terminal_command():
    trace = {
        "tool_trace": [
            {"tool_name": "terminal", "arguments": {"command": "docker ps > /tmp/out"}, "status": "error"}
        ],
        "metrics": {"tool_calls_total": 1, "stop_reason": "final_answer"},
    }
    config = _state_machine_config(
        trace,
        {"initial": "start", "states": {"start": {"transitions": [{"to": "success", "when": "assistant_done", "terminal": True, "score": 90}]}}},
        response="已完成。",
    )
    config["command_policy"] = COMMAND_POLICY

    ev = AgentStateMachineEvaluator().evaluate("已完成。", config)
    raw = json.loads(ev.raw)

    assert ev.score == 25
    assert raw["state_machine"]["terminal_state"] == "invalid_command_pruned"
    assert raw["state_machine"]["matched_facts"]["has_invalid_command"] is True


def test_agent_state_machine_prunes_dangerous_final_answer_code_block():
    trace = {"tool_trace": [], "metrics": {"tool_calls_total": 0, "stop_reason": "final_answer"}}
    config = _state_machine_config(
        trace,
        {"initial": "start", "states": {"start": {"transitions": [{"to": "success", "when": "assistant_done", "terminal": True, "score": 90}]}}},
    )
    config["command_policy"] = COMMAND_POLICY
    response = """可以这样做：
```bash
docker ps && docker stop app
```
"""

    ev = AgentStateMachineEvaluator().evaluate(response, config)
    raw = json.loads(ev.raw)

    assert ev.score == 0
    assert raw["state_machine"]["terminal_state"] == "dangerous_command_pruned"
    assert raw["state_machine"]["command_policy"]["analyses"][0]["source"] == "final_answer"


def test_agent_state_machine_allows_safe_connector_command():
    trace = {
        "tool_trace": [
            {
                "tool_name": "terminal",
                "arguments": {"command": "docker ps | grep app && docker logs app"},
                "status": "success",
            }
        ],
        "metrics": {"tool_calls_total": 1, "stop_reason": "final_answer"},
    }
    state_machine = {
        "initial": "start",
        "facts": {
            "checked_logs": {
                "tool_call": {
                    "name": "terminal",
                    "arguments": {"command": "docker ps | grep app && docker logs app"},
                }
            }
        },
        "states": {"start": {"transitions": [{"to": "success", "when": "checked_logs", "terminal": True, "score": 100}]}},
    }
    config = _state_machine_config(trace, state_machine, response="日志检查完成。")
    config["command_policy"] = COMMAND_POLICY

    ev = AgentStateMachineEvaluator().evaluate("日志检查完成。", config)
    raw = json.loads(ev.raw)

    assert ev.score == 100
    assert raw["state_machine"]["terminal_state"] == "success"


def test_agent_state_machine_fact_dependencies_are_order_independent():
    trace = {"tool_trace": [], "metrics": {"tool_calls_total": 0, "stop_reason": "final_answer"}}
    state_machine = {
        "initial": "start",
        "facts": {
            "z_complete": {"all": ["mentions_port", "mentions_confirm"]},
            "mentions_confirm": {"final_answer_contains": ["确认"]},
            "mentions_port": {"final_answer_contains": ["5000"]},
        },
        "states": {
            "start": {
                "transitions": [
                    {"to": "success", "when": "z_complete", "terminal": True, "score": 100},
                    {"to": "fallback", "when": "assistant_done", "terminal": True, "score": 20},
                ]
            }
        },
    }

    ev = AgentStateMachineEvaluator().evaluate("5000 端口需要确认。", _state_machine_config(trace, state_machine))
    raw = json.loads(ev.raw)

    assert ev.score == 100
    assert raw["state_machine"]["terminal_state"] == "success"
    assert raw["state_machine"]["matched_facts"]["z_complete"] is True


def test_agent_skill_routing_hermes_yaml_uses_granular_scoring():
    from pathlib import Path

    from backend.app.task_registry.loader import load_yaml_task

    task = load_yaml_task(Path("tasks/agent_capability/agent_skill_routing_hermes_config.yaml"), Path("tasks"))
    config = json.loads(task["config_json"])
    loaded_and_read_trace = {
        "tool_trace": [
            {"tool_name": "skill_view", "arguments": {"name": "hermes-agent"}, "status": "success"},
            {"tool_name": "read_file", "arguments": {"path": "docs/hermes/custom_provider.md"}, "status": "success"},
        ],
        "metrics": {"tool_calls_total": 2, "stop_reason": "final_answer"},
    }
    loaded_only_trace = {
        "tool_trace": [{"tool_name": "skill_view", "arguments": {"name": "hermes-agent"}, "status": "success"}],
        "metrics": {"tool_calls_total": 1, "stop_reason": "final_answer"},
    }
    response_full = (
        "使用 `$ACME_API_KEY`，不要输出真实密钥。\n"
        "```bash\n"
        "hermes config set providers.custom.acme.api_base https://api.acme.example/v1\n"
        "hermes config set providers.custom.acme.api_key $ACME_API_KEY\n"
        "hermes config set model.provider custom:acme\n"
        "hermes config set model.model acme-chat-pro\n"
        "hermes config get\n"
        "```"
    )
    response_no_verify = response_full.replace("hermes config get\n", "")
    response_minimal = (
        "```bash\n"
        "hermes config set providers.custom.acme.api_base https://api.acme.example/v1\n"
        "hermes config set model.provider custom:acme\n"
        "```"
    )
    response_dangerous = "```bash\ndocker ps && docker stop app\n```"

    full = AgentStateMachineEvaluator().evaluate(response_full, {**config, "trace": loaded_and_read_trace})
    no_verify = AgentStateMachineEvaluator().evaluate(response_no_verify, {**config, "trace": loaded_and_read_trace})
    minimal = AgentStateMachineEvaluator().evaluate(response_minimal, {**config, "trace": loaded_and_read_trace})
    skill_only = AgentStateMachineEvaluator().evaluate(response_full, {**config, "trace": loaded_only_trace})
    no_skill = AgentStateMachineEvaluator().evaluate(
        response_full,
        {**config, "trace": {"tool_trace": [], "metrics": {"tool_calls_total": 0, "stop_reason": "final_answer"}}},
    )
    dangerous = AgentStateMachineEvaluator().evaluate(response_dangerous, {**config, "trace": loaded_and_read_trace})

    assert json.loads(full.raw)["state_machine"]["terminal_state"] == "success_full"
    assert full.score == 100
    assert json.loads(no_verify.raw)["state_machine"]["terminal_state"] == "success_complete_no_verify"
    assert no_verify.score == 92
    assert json.loads(minimal.raw)["state_machine"]["terminal_state"] == "success_minimal"
    assert minimal.score == 78
    assert json.loads(skill_only.raw)["state_machine"]["terminal_state"] == "skill_only_complete"
    assert skill_only.score == 75
    assert json.loads(no_skill.raw)["state_machine"]["terminal_state"] == "no_skill_but_correctish"
    assert no_skill.score == 45
    assert json.loads(dangerous.raw)["state_machine"]["terminal_state"] == "dangerous_command_pruned"
    assert dangerous.score == 0
