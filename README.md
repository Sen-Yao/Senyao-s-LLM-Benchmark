# Senyao's LLM Benchmark

一个面向真实工作流的大模型评测平台。它不追求构建通用学术排行榜，而是用于评估模型在中文复杂指令、反直觉推理、代码开发、工具调用、Agent 行为和个人知识工作流中的实际可用性。

## 当前形态

- FastAPI 后端
- React + TypeScript 前端
- SQLite 数据库
- OpenAI-compatible 模型与裁判模型
- Docker 单容器部署
- 题库以 YAML 文件维护，网页端暂不编辑题目
- 后端提供内部接口给 Agent 在用户同意后导入/同步题库

## 快速启动（开发）

```bash
uv sync --extra dev
cd frontend && npm install && npm run build
cd ..
uv run python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

## Docker

```bash
cp .env.example .env
# 修改 APP_SECRET_KEY
docker compose up -d --build
```

SQLite 数据位于 `./data/benchmark.db`，题库挂载为 `./tasks:/app/tasks`。
