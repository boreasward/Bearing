-- 033: 删除 v2 旧系统的业务表（v2 下线设计 §5.6）。可重跑：全部 IF EXISTS，不用 CASCADE。
-- 真实库执行需用户单独授权：先 pg_dump 备份（含这些表的数据，删除不可恢复），再执行本文件，最后核对
-- 保留的事实表与 v3_ 表行数不变。步骤见 docs/operations/migrations-and-backup.md。
-- python -m investment_assistant.migrate 在这些表有数据时会拒绝执行本文件，除非设置环境变量
-- INVESTMENT_ASSISTANT_ALLOW_DROP_V2_TABLES=1（先备份，再设置）。
--
-- 保留：price_bars、news_items、news_item_tickers、filings、earnings_dates、macro_observations、
-- sector_classifications、us_research_observations、us_research_collection_state、data_fetch_runs、
-- watchlist_items、feature_flag_changes（core.features.current 仍读它）、七张 v3_ 表。
--
-- 外键：迁移 027、029、030 加的外键全部在被删表之间，保留表上没有指向被删表的外键
--（唯一指向保留表的是 data_requests.fetch_run_id → data_fetch_runs，随 data_requests 一起删），
-- 所以不需要先 ALTER TABLE ... DROP CONSTRAINT；下面的顺序按依赖从叶到根，不依赖 CASCADE。
-- LangGraph 的四张检查点表不是迁移建的，按 search_path 里的 schema 删除（有就删，没有就跳过）。

BEGIN;

-- 调度、通知、复盘产出（无其它表引用它们）
DROP TABLE IF EXISTS scheduled_jobs;
DROP TABLE IF EXISTS job_reports;
DROP TABLE IF EXISTS notify_settings;
DROP TABLE IF EXISTS signal_candidates;
DROP TABLE IF EXISTS weekly_reviews;
DROP TABLE IF EXISTS monthly_reviews;
DROP TABLE IF EXISTS market_regimes;
DROP TABLE IF EXISTS rule_backchecks;
DROP TABLE IF EXISTS review_pushes;
DROP TABLE IF EXISTS screen_results;
DROP TABLE IF EXISTS pool_tiers;
DROP TABLE IF EXISTS user_inputs;

-- 成长闭环（027）叶子
DROP TABLE IF EXISTS knowledge_status;
DROP TABLE IF EXISTS knowledge_draft_verdicts;
DROP TABLE IF EXISTS review_card_verdicts;
DROP TABLE IF EXISTS knowledge_citations;

-- Agent 连接与每日流程（028）、可执行计划（029）、账户（030）叶子
DROP TABLE IF EXISTS agent_connections;
DROP TABLE IF EXISTS agent_connection_events;
DROP TABLE IF EXISTS agent_attempt_resets;
DROP TABLE IF EXISTS chain_retriggers;
DROP TABLE IF EXISTS plan_notes;
DROP TABLE IF EXISTS account_param_changes;
DROP TABLE IF EXISTS paper_chain_done;
DROP TABLE IF EXISTS account_snapshots;
DROP TABLE IF EXISTS cash_flows;
DROP TABLE IF EXISTS holding_status;
DROP TABLE IF EXISTS account_equity_days;
DROP TABLE IF EXISTS paper_orders;

-- 五层（031）与运行记录
DROP TABLE IF EXISTS data_requests;
DROP TABLE IF EXISTS agent_tool_calls;
DROP TABLE IF EXISTS research_versions;
DROP TABLE IF EXISTS portfolio_checks;
DROP TABLE IF EXISTS stage_runs;
DROP TABLE IF EXISTS roadmap_goals;
DROP TABLE IF EXISTS roadmap_versions;
DROP TABLE IF EXISTS roadmap_decisions;
DROP TABLE IF EXISTS roadmap_evidence;
DROP TABLE IF EXISTS roadmap_briefs;
DROP TABLE IF EXISTS screen_runs;

-- 中间层：被上面的表引用过，自己还引用 agent_runs / trade_plans / trades 一侧
DROP TABLE IF EXISTS knowledge_versions;
DROP TABLE IF EXISTS plan_reviews;
DROP TABLE IF EXISTS paper_decisions;
DROP TABLE IF EXISTS research_requests;
DROP TABLE IF EXISTS roadmap_proposals;
DROP TABLE IF EXISTS knowledge_drafts;
DROP TABLE IF EXISTS agent_plan_versions;
DROP TABLE IF EXISTS review_card_drafts;
DROP TABLE IF EXISTS discussion_turns;
DROP TABLE IF EXISTS knowledge_items;
DROP TABLE IF EXISTS discussions;
DROP TABLE IF EXISTS review_cards;

-- 根：交易、决策、计划、账户与 Agent 运行
DROP TABLE IF EXISTS trades;
DROP TABLE IF EXISTS signal_decisions;
DROP TABLE IF EXISTS accounts;
DROP TABLE IF EXISTS signal_briefs;
DROP TABLE IF EXISTS agent_runs;
DROP TABLE IF EXISTS trade_plans;

-- LangGraph 检查点（不是迁移建的）
DROP TABLE IF EXISTS checkpoint_writes;
DROP TABLE IF EXISTS checkpoint_blobs;
DROP TABLE IF EXISTS checkpoints;
DROP TABLE IF EXISTS checkpoint_migrations;

COMMIT;
