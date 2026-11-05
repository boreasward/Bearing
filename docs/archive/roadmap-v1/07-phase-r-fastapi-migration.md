# 07 — Phase R：API 层迁移 FastAPI（重构地基）

> **已归档（2026-10-23）**：本文属于 roadmap v1，已被 [roadmap v2](../v2/roadmap/roadmap.md) 取代，仅作历史参考，不再更新。

> 创建：2026-07-04 ｜ 状态校准：2026-10-17 ｜ `engineering_done`

FastAPI / uvicorn、`api/app.py`、分域 `APIRouter`、OpenAPI 及事件研究前端/说明文档均已落地，**不再是当前待开发阶段**。下文保留迁移时的历史设计；其中 36 路由、190 测试和“前端不动”描述只适用于当时的迁移范围，不是今天的数量或后续变更限制。

本轮没有 HTTP/UI 运行验收，仍为 `runtime_unverified`。当前工作树修复的最终回归数字待 [实施状态](implementation-status-2026-10-17.md)回填；FastAPI 已完成不代表 Phase 0 Gate 已通过，也不代表 Phase 2 已落地。

---

## 为什么插入 Phase R

Phase 2 闭环（[03](03-phase2-plan-execution-loop.md)）是表单密集型 CRUD：计划录入、交易日志、偏离标注，全部需要请求体校验。当时自研 HTTP 层（stdlib `http.server` + 手写路由表 + 手写参数解析）没有校验设施，每个表单字段都要手写类型检查。因此决定先迁移、后开发（现已实施）：

- 迁移范围小：36 条路由全是薄封装（`api/routes/* → services/*`），services 层不动；
- 契约已被钉住：190 个离线测试覆盖全部路由行为，迁移即「换壳跑同一套断言」；
- Pydantic 校验在 Phase 2 的收益最大，OpenAPI 文档顺带解决「每个功能点提供功能说明」的一半。

## 迁移边界

| 组件 | 处置 |
|------|------|
| `api/http.py`（请求解析 / ApiResponse） | **替换**：FastAPI 请求模型 + JSONResponse |
| `api/router.py`（手写路由表） | **替换**：按域拆 `APIRouter`（market / tickers / strategies / hermes / research / jobs / watchlist / settings / status / runs） |
| `api/server.py`（stdlib http.server） | **替换**：uvicorn；入口模块名不变（`python -m investment_assistant.api.server`），systemd 单元不改 |
| `api/auth.py` token 校验 | **替换**：FastAPI dependency；fail-closed 公网绑定检查保留为启动前检查 |
| `api/static_files.py` | **保留**：路径穿越防护 / `/status` 页 / 前端未构建 503 提示，挂 catch-all 路由，不换 `StaticFiles` |
| `services/*`、`db.py`、`migrate.py`、`tasks/scheduler.py`、Svelte 5 前端 | **不动** |

新增依赖仅：`fastapi`、`uvicorn`、`httpx`（TestClient 测试用）。不引入 SQLModel / alembic / APScheduler——自研迁移器与调度器有测试且在生产运行，替换它们只有整齐感收益。

## 行为对齐清单（契约，逐项验证）

1. 36 条路由路径与响应 JSON 不变——前端 `web/src/lib/api.ts` 零改动。
2. 无 `INVESTMENT_ASSISTANT_DATABASE_URL` 时只读端点返回空 + `{"degraded": true}`，不崩。
3. `/api/runs/{id}` SSE 用 `StreamingResponse` 包装现有 `tasks/runner.subscribe()` 队列。
4. 认证：无认证时拒绝绑定公网地址（fail-closed）；token 校验行为不变。
5. 错误不静默：外部失败结构化返回 + 日志（沿用现约定）。

## 测试与契约策略

- 现有 API 测试从 `router.dispatch(...)` 改为 `TestClient` 调用，**断言不变**——它们就是迁移的验收契约。
- services / db / tasks 测试完全不动。
- 完成标准：`pytest` 全绿 + `cd web && npm test` 绿 + 手动验证 `GET /api/status`、SSE 流、`/` 静态页各一次。

## Phase R 收尾任务（DoD 存量欠账）

以下两项原欠账已在工程上补齐。使用说明见 [事件研究](../v2/features/legacy/README.md)；本轮真实 HTTP/UI 验收仍待完成，不等于入口缺失：

1. 前端「策略」区加事件研究面板（调既有 `GET /api/research/event-study`），含手动触发入口。
2. 文档补「事件研究：功能说明 + 如何验证示例」（CLI 与 API 两条路径）。

## 完成标志

- 全量测试绿，前端零改动可用；
- `/docs`（OpenAPI）可浏览全部端点——每个端点自带请求/响应模型说明；
- 事件研究在前端可见、可手动触发、文档可验证；
- 之后 Phase 1/2/3 的全部新路由直接以 FastAPI + Pydantic 编写。
