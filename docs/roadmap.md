# Roadmap（v3）

v3 重做的目标（spec §2 目标 1–3）：入口是 agent——每天一份简报回答「最近行情适合交易什么」，落到个股；先出草稿（规则算出的个股名单，不调模型，事实任务一结束就存在），再自动取前 5 只做二次确认并输出交易计划，之后可以在同一个工作区对话追问。

| 步 | 内容 | 状态 |
| --- | --- | --- |
| 0 | Pi 可行性验证 | 已完成（2026-11-05 十项检查 10/10 通过，见 [operations/pi-unattended.md](operations/pi-unattended.md)） |
| 1 | 事实层与工具面 | 已完成（代码与测试落地；迁移 032 已在真实库执行；2026-11-06 真实库成功路径试跑通过：名单 20 只、含采集 3 分 20 秒、重算一致，真实 Pi 客户端冒烟通过，见 [operations/v3-facts.md](operations/v3-facts.md)） |
| 2 | 每日简报 | 未开始 |
| 3 | 页面服务与对话 | 未开始 |
| 4 | 概念学习 | 未开始 |
| 5 | 旧系统下线 | 已提前完成（2026-11-06）：后端、前端、概念卡与文档已移除或归档。迁移 033（删 v2 的表）已于 2026-11-06 在真实库执行（先 `pg_dump` 备份，记录见 [operations/migrations-and-backup.md](operations/migrations-and-backup.md)）；旧服务已 `launchctl bootout`，未重新加载（见 [operations/local-service.md](operations/local-service.md)） |

**顺序改为 0 → 1 → 5 → 2 → 3 → 4。** 第 1 步完成后用户决定以 v3 为主要迭代方向，接受第 2 步完成前没有每日推送和页面，所以第 5 步提前做，让第 2、3 步在只剩 v3 的代码上开工。删除的依据、范围与和 v3 设计的出入见 [specs/2026-11-06-v2-removal-design.md](specs/2026-11-06-v2-removal-design.md)，执行过程见 [plans/2026-11-06-v2-removal.md](plans/2026-11-06-v2-removal.md)。

- 设计：[spec](specs/2026-11-05-agent-first-rebuild-design.md)（步骤与变化见 §15.6）
- 第一阶段实施：[plan](plans/2026-11-05-v3-facts-and-tools.md)（第 0、1 步与文档归档）
- v2 的路线与验收记录（已归档，只读）：[roadmap](archive/v2/roadmap/roadmap.md)

本文件直接修改维护。
