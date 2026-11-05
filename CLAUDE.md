# 项目约定

- **主干开发为主。** 默认直接在 `main` 上开发、测试和提交，不另开分支或 worktree。只有用户明确要求隔离时才用分支或 worktree，完成后快进合并回 `main`，并删除该分支和 worktree。
- **文档位置。** 设计 spec 放 `docs/specs/YYYY-MM-DD-<topic>-design.md`，实施 plan 放 `docs/plans/YYYY-MM-DD-<topic>.md`，写成后只增不改；功能、架构与运维文档随代码同一个提交更新。分类与维护规则见 `docs/README.md`。
- **Roadmap 记录。** roadmap 是仓库里的 `docs/roadmap.md`，直接修改。v2 的路线与功能文档已归档到 `docs/archive/v2/`，只读。spec / plan 仍在 `docs/specs/`、`docs/plans/`。

# plan 与执行约定

- **按层合并 Task，不按模块拆。** 实施 plan 把同一层的工作合成一个 Task（例如：迁移与仓储、纯函数层、编排与 API、前端、收尾），每个 Task 只做一轮审查；只有文件不重叠、可并行的部分才单独成 Task。
- **plan 里标出并行关系。** 在「执行顺序与依赖」里明确写出哪些 Task 的文件互不重叠、可以同时做（典型：前端与后端），以及共享的接口文件由谁负责。
- **测试分级。** 每个 Task 只跑与本 Task 相关的测试文件；离线全量 pytest、一次性库全量 pytest、前端 `npm test && npm run build` 三套只在收尾 Task 跑一次。写进 plan 的 Global Constraints。
- **审查一轮。** 每个 Task 派一轮审查，修复后由编排者看 diff 与相关测试确认，不派复审；全量 code review 只在收尾 Task 做一次。
- **子代理分工。** 实现子代理用别名 `sonnet`，审查与设计用 `opus`（Agent 工具只认别名，不认完整模型 id）。若别名子代理报 404 且错误里的模型名是 `ccs-…`，是 cc-switch 把 `ANTHROPIC_DEFAULT_*_MODEL` 重新写进了 `~/.claude/settings.json`，删掉那几行再开新 session。
