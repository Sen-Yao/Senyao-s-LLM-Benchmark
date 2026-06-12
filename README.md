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

项目建议使用 Python 3.11；仓库内的 `.python-version` 用于让 `uv`、pyenv 等工具选择与 Docker 运行时一致的解释器。

## Docker

```bash
cp .env.example .env
# 修改 APP_SECRET_KEY
docker compose up -d --build
```

SQLite 数据位于 `./data/benchmark.db`，题库挂载为 `./tasks:/app/tasks`。

当前默认数据库是 SQLite，不需要 MySQL。若部署到 Unraid/Yggdrasil 这类单机 Docker 环境，建议保持 `DATABASE_URL=sqlite:////app/data/benchmark.db` 并将 `./data` 映射到持久化目录。只有在确实需要多实例并发写入或集中数据库管理时，才考虑切换到 MySQL/PostgreSQL；切换前还需要补充相应 Python driver、迁移策略和连接池配置。


## 图标与 Docker UI

Web 页签、侧栏品牌标识与 Web Manifest 使用 `frontend/public/benchmark-logo.svg`。
该 SVG 基于 Tabler Icons 的 gauge 图形语言二次定制；Tabler Icons 为 MIT License，Copyright (c) 2020-2026 Paweł Kuna。迁移到 Yggdrasil / Docker UI 时，可直接使用同一个 SVG 作为应用图标：

```text
/app/frontend/dist/benchmark-logo.svg
```

若 Docker UI 需要外部 URL，部署后使用：

```text
https://<your-domain>/benchmark-logo.svg
```
