# Cross-market Medium-term Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Status (2026-10-16):** Tasks 1–5 independently approved; Task 6 local deployment, real-source verification and browser checks complete on application `291aaff`. Final documentation is recorded in `docs/cross-market-research.md`; user acceptance is pending. One residual Minor freshness-warning false positive remains explicitly parked; exact native confirm/unload interaction is covered by component tests only.

**Goal:** Deliver A-share watchlist management and source-backed US research for the next 2–4 quarters, preserving the existing local investment assistant.

**Architecture:** Keep the shared watchlist and existing A-share ingestion. Add a focused `us_research` package for normalized SEC facts, period calculations, source snapshots and aggregation; share versioned observation cards across markets. Read endpoints use persisted data; explicit sync handles remote acquisition and reports failures independently.

**Tech Stack:** Existing Python/FastAPI/psycopg/PostgreSQL, Svelte 5/TypeScript/ECharts, pytest/Vitest; reuse installed dependencies.

**Spec:** `docs/superpowers/specs/2026-10-16-cross-market-medium-term-research-design.md`

## Global Constraints

- 本次完善以3–12个月为研究周期，围绕未来2–4个季度检查经营假设是否兑现。
- 本地目录沿用`/Users/chenjianrui/workspaces/investment-assistant`，服务地址`http://127.0.0.1:8787`，启动沿用`./start.command`。
- 沿用现有688008记录，不自动增加其他股票。
- 数据修订追加版本，原始响应或文件摘要可追溯；派生值保存全部输入记录标识。
- 无一致预期来源时显示“未接入一致预期”，不把公司指引称为市场预期。
- 本次不额外启用定时任务。研究刷新不发送外部通知，不调用交易。
- Worktree: `/Users/chenjianrui/.codex/worktrees/cross-market-research/investment-assistant`; source baseline commit `00a8b2e`. Source files copied from the current dirty checkout; no credentials or runtime data copied. `.venv` and `web/node_modules` are local dependency symlinks and must never be staged.
- Implementers work only in this worktree, stage explicit source paths, and never spawn subagents. Production data writes and deployment verification belong to Task 6 after review.
- Preserve existing 8 US watchlist entries, the 688008 card/version, original SEC index and job/notification configuration. Do not expose secrets in tools, reports, fixtures or commits.
- Meaningful tests exercise real calculations, validation, storage or rendered behavior. Mock external I/O only. Record expected failing tests before implementation and passing tests after it.

## File and interface map

| Owner | Files | Responsibility |
| --- | --- | --- |
| Task 1 | `services/watchlist.py`, `api/routes/watchlist.py`, `ashare/service.py`, `api/routes/ashares.py`; `Watchlist.svelte`, `SecurityPicker.svelte`, `Settings.svelte`, `AShares.svelte`, `lib/research-navigation.ts` | One watchlist, market filters, identity confirmation, A-share selection/batching |
| Task 2 | `us_research/domain.py`, `metrics.py`, `sources.py`, `templates.py`, `__init__.py` | SEC normalization, fiscal periods, derived metrics, source adapters and industry templates |
| Task 3 | `us_research/repository.py`, `service.py`, `api/routes/us_research.py`, `tasks/us_research.py`, migration 014, `tasks/filings.py` | Durable acquisition, local reads, collection status, job integration |
| Task 4 | `research/cards.py`, `ashare/cards.py`, `us_research/guidance.py`, `ResearchCard.svelte` | Market-aware evidence/scenarios, guidance revisions/comparison, old-card compatibility |
| Task 5 | `USResearch.svelte`, `FinancialSeries.svelte`, `GuidanceEditor.svelte`, `Data.svelte`, `Strategy.svelte`, `SideNav.svelte`, `api.ts` | Complete user research flow and visible source/coverage handling |
| Task 6 | `scripts/verify_cross_market.py`, `docs/cross-market-research.md`, `README.md`, `docs/README.md` | Data backfill, local deployment/read-back, browser checks, evidence and delivery |

Task interfaces are additive. A task may introduce a focused helper file when its responsibility is stated in the report; no unrelated refactors.

### Task 1: Market-aware watchlist and A-share navigation

**Files:** Modify the Task 1 owners above and `investment_assistant/db.py` for atomic watchlist updates; tests `tests/test_services_watchlist.py`, `tests/test_ashare_sources.py`, `tests/test_watchlist_markets.py`, `web/src/routes/AShares.test.ts`, new `web/src/lib/components/Watchlist.test.ts` and `tests/test_watchlist_api.py` if needed.

**Interfaces:**
- `watchlist_rows(market: str | None = None, *, enriched: bool = False) -> list[dict]`; existing no-argument consumers retain their record shape. `GET /api/watchlist?market=CN|US&enriched=true` returns `rows,count`, rows may add `market,name,board,latest_period,datasets,next_event`.
- Existing watchlist POST is an upsert for status/thesis/tags. Add optional optimistic `expected_updated_at` for editing from the new UI; enforce it in one conditional database UPDATE with `WHERE updated_at = expected_updated_at`, not a service read-then-upsert. Return 409 on conflict/deleted record; absent identity must not accidentally create on edit. Preserve omitted fields and add TEMP-table conflict/deleted-edit tests.
- `GET /api/watchlist/resolve?symbol=600519` returns identity candidates without adding to the list. Only explicitly selected complete symbols are persisted.
- `SecurityPicker` consumes `market: 'CN'|'US'`, `value: string`, `onchange(symbol: string): void`, optional `beforeChange(): boolean`; loads the same watchlist API. Navigation helpers preserve market/symbol through hash query strings without changing router page/sub semantics.

- [x] Write failing tests for CN/US isolation, paused-only US watchlist not resurrecting defaults, invalid/ambiguous identity, edits not overwriting thesis/tags, >10 A-share sync and per-stock failure continuation. Example invariant:
```python
def test_paused_us_symbols_do_not_return_from_config(monkeypatch):
    monkeypatch.setattr(watchlist, 'watchlist_rows', lambda: [
        {'ticker': 'MU', 'status': 'paused'}])
    assert watchlist.current_watchlist() == []
```
Also verify a configured database with an empty list stays empty; the no-database config fallback remains supported.
- [x] Run focused pytest and component tests, record expected failures.
- [x] Implement market derivation from validated security identity. Enrichment reads local profiles/states only; listing cannot start ingestion. Preserve default compatibility and make invalid query markets a 4xx.
```python
def market_of(ticker: str) -> str:
    return 'CN' if is_cn_symbol(ticker.strip().upper()) else 'US'
# Snapshot all active CN entries, de-duplicate, and process chunks under one ingestion run.
for offset in range(0, len(symbols), 10):
    batch = symbols[offset:offset + 10]
```
- [x] Implement shared list editor with All/CN/US tabs, name/board/coverage/event columns, edit/pause/resume/remove and server read-back. A-share picker derives initial choice from URL then first available list entry; empty lists do not fetch 688008. Remove fixed semiconductor tag and default benchmark to CSI300. Guard asynchronous requests by request identity.
- [x] Test alternate A-share selection, stale-response rejection, empty state, refresh/deep links and preservation of user edits; run relevant suites and commit explicit Task 1 paths.

### Task 2: Source-backed US fiscal metrics and adapters

**Files:** Create Task 2 owners; `tests/test_us_research_domain.py`, `tests/test_us_research_sources.py`, local sanitized fixtures under `tests/fixtures/us_research/`.

**Interfaces:**
- `stock_id(ticker: str) -> str` returns `US:stock:MU` and rejects CN/index/path-like inputs; `normalize_companyfacts(payload: dict, *, ticker: str, allowed_forms: list[str] | None = None) -> list[dict]` produces normalized observations.
- Each observation: `metric,value,unit,basis,start,end,period_kind,form,accession,filed,source_url,concept,source_locator,source_hash` (numeric values finite; start nullable for instant values). Keep alternative filings/versions, not only a flattened latest value.
- `financial_series(observations: list[dict], *, limit: int = 12) -> list[dict]` returns ascending fiscal quarters `{period,start,end,days,metrics,ttm,warnings}`. Each metric is `{value,unit,basis,method,input_ids,sources,missing_reason}`; missing values are explicit `None`.
- `performance_view(bars: list[dict], benchmarks: dict[str,list[dict]]) -> dict` computes 3/6/12 calendar-month coverage-aware returns, drawdowns and common-date relative comparisons. `valuation_view(financials: list[dict], market: dict) -> dict` uses verified compatible market-cap inputs and positive earnings only.
- `USSources.companyfacts(ticker) -> dict` and `.market(ticker) -> dict`; source errors are sanitized. Market payload `{bars,benchmarks,quote,source_url,collected_at,limitations}`; bar fields `date,close,adjusted_close,volume`, quote values include explicit date/currency/share-class basis. `companyfacts` honors existing SEC form rules and returns normalized observations plus raw-source hash/coverage.
- `research_template(ticker: str) -> list[dict]` returns current 8 company templates; unknown tickers receive generic research checks. Templates are questions, never fabricated facts.

- [x] Write hand-computed fixtures first: cumulative CFO 10/25/45/70 implies single quarters 10/15/20/25; period-end inventory remains 80; missing Q2 prevents Q3 difference and TTM; annual EPS cannot be differenced. Include 52/53-week periods, duplicate/conflicting/restated facts, overlapping taxonomy aliases, split basis, zero/negative growth denominator, non-USD facts, missing instant balances, and nonpositive PE.
```python
def test_no_ttm_across_missing_quarter():
    rows = financial_series(observations_with_q2_missing())
    assert rows[-1]['ttm']['revenue']['value'] is None
```
- [x] Run failing focused tests, then build normalized concept registry (revenue, gross profit/costs, operating/net income, diluted EPS, CFO, cash capex, SBC, cash, investments, debt, AR, inventory, shares, repurchases). Make debt components mutually exclusive; keep unknown coverage unknown.
Net income must retain attribution: consolidated, attributable to parent, or available to common shareholders. PE requires verified common-shareholder earnings, not an interchangeable consolidated alias; test different consolidated/common values and an unverified attribution remaining unavailable.
- [x] Derive only comparable amounts: direct 60–120-day quarters first, coherent YTD differencing second; fiscal Q4 from annual minus nine months. Align actual start/end dates and preserve source versions. TTM requires four contiguous quarters; margins divide aggregate amounts. Growth is null for nonpositive base. FCF=CFO−cash PPE; average-balance turns require matching instant dates.
```python
fcf = cfo - cash_capex if cfo is not None and cash_capex is not None else None
pe = market_cap / net_income_ttm if compatible and net_income_ttm > 0 else None
```
- [x] Use existing SEC downloader identity/cache and `data.http` retries; preserve User-Agent internally. Validate returned company identity. Fetch raw and adjusted Yahoo OHLCV together with provenance; do not infer price-to-shares compatibility or fabricate historical market cap. Keep return and valuation bases separate. Local raw snapshots will be persisted by Task 3.
- [x] Run domain/source tests, existing filing-analysis tests, self-review and commit explicit Task 2 paths.

### Task 3: Durable US research collection and read APIs

**Files:** Create Task 3 owners; modify `api/routes/__init__.py`; tests `tests/test_us_research_service.py`, `tests/test_us_research_api.py`, `tests/test_us_research_postgres.py`.

**Interfaces:**
- `GET /api/us-research/{ticker}?as_of=<iso>` returns `{security_id,ticker,currency,financials,market,valuation,statuses,template,card,guidance,filings,pit}`. `financials` follows Task 2; local reads never invoke remote I/O.
- `POST /api/us-research/sync` body `{symbols?: string[], datasets?: ['financials','market','filings'], retry_failed?: bool}` returns `{run_id,status}`; absent symbols means all active US watchlist entries. Read job result through existing task API.
- `service.sync(symbols=None, *, datasets=None, retry_failed=False, source=None) -> dict` returns per-stock/dataset results and `notifications='disabled'`. `service.view(ticker, *, as_of=None) -> dict`. `service.refresh_local_filings(tickers: list[str]) -> dict` parses available local files without remote requests or notifications.
- Repository stores datasets independently with append-only content revisions and observation IDs. Source raw snapshots live under ignored `.local/us-research/raw/`. Connection helper uses existing DB env internally.

- [x] Write failing tests for append-only A→B→A versions, deduplication, per-dataset commits, old-data retention after source failure, historical cutoff, input validation, paused-symbol exclusion, read-only GET and source exceptions not exposing credentials.
- [x] Add migration `014_us_research.sql`: `us_research_observations` (security/dataset/natural-key/revision/payload/source/times), `us_research_collection_state`; no changes to existing watchlist or card data. Guidance uses the same versioned observation store with dataset `guidance`.
```sql
CREATE TABLE IF NOT EXISTS us_research_observations (
  id BIGSERIAL PRIMARY KEY, security_id TEXT NOT NULL, dataset TEXT NOT NULL,
  natural_key TEXT NOT NULL, revision TEXT NOT NULL, payload JSONB NOT NULL,
  disclosed_at TIMESTAMPTZ, first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
- [x] Implement storage/read/collection-state, advisory-lock protection and background job submission. Validate symbols/datasets before scheduling. Persist failures without erasing successful data. Use observation/disclosure cutoff together; expose `strict=false`.
- [x] Integrate existing SEC success flow with local-only extraction; retain original results/notifications behavior. Reuse `filings.analysis` facts and evidence locators. Catch parser errors per document, expose them in research coverage rather than silently swallowing.
- [x] Run service/API tests and PostgreSQL TEMP-table tests using an explicitly supplied test URL; no production business-table writes. Commit explicit Task 3 paths.

### Task 4: Cross-market cards, industry evidence and guidance history

**Files:** Create/modify Task 4 owners; update US API routes/service and existing A-share card route compatibility; tests `tests/test_cross_market_cards.py`, `tests/test_us_guidance.py`, `web/src/lib/components/ResearchCard.test.ts`.

**Interfaces:**
- Existing `GET/PUT /api/research/observation-cards/{symbol}` accepts A-share full symbol and US ticker. Existing A-share JSON schema stays readable/savable; new scenario uses `profit,currency` while legacy `profit_cny` maps explicitly to CNY. Reject contradictory currency fields.
- Evidence may add `metric,value,unit,period,source_locator,basis`; textual/range evidence remains allowed. Validate referenced observation and filing belongs to the selected market/security.
- `GET/POST /api/us-research/{ticker}/guidance` lists/appends guidance records. Request: `{metric,target_start,target_end,basis,unit,low,high,issued_at,source_url,source_locator,note,supersedes_id?}`; numbers finite, low<=high, required source, dates coherent. No destructive revision.
- `guidance.comparisons(records, financials) -> list[dict]` binds actual to the same target interval/metric/unit/basis, reports actual/low/high/midpoint and in/below/above range or not comparable. Never call guidance consensus.

- [x] Write failing tests for legacy CNY card roundtrip with unchanged values, USD scenario validation, wrong-security evidence rejection, stale-version 409, arbitrary watchlist US links, guidance revisions preserving original, and actual/guide mismatch remaining uncomputed.
```python
def test_different_basis_does_not_produce_beat():
    result = comparisons([gaap_guidance()], non_gaap_actual())
    assert result[0]['status'] == 'not_comparable'
```
- [x] Extract shared card validation/persistence into `research/cards.py`; leave backward-compatible A-share facade. Support all valid US tickers in research links, validate filing identity. Keep old version rows untouched; GET may map schema in memory without auto-saving.
- [x] Add typed, append-only guidance service over Task 3 repository. Preserve original issuance and newly observed time; require per-security related supersedes record. Source-backed industry values may be entered in the card rather than requiring an unsafe generic parser.
- [x] Update card UI with market/currency-aware labels, optional metric/period/locator evidence fields and dirty-state protection on symbol/navigation changes. No default investment assumptions or numerical scenarios for new cards.
- [x] Run card/guidance/database/component tests and commit explicit Task 4 paths.

### Task 5: Complete US research UI and cross-market flow

**Files:** Create/modify Task 5 owners; tests `web/src/routes/USResearch.test.ts`, `web/src/lib/components/GuidanceEditor.test.ts`, update app/router/API tests where behavior changes.

**Interfaces:** Use Task 1 `SecurityPicker`, Task 3 persisted view/sync APIs, Task 4 card/guidance APIs. Both data and observation page preserve `market` and `symbol` in hash query. `FinancialSeries` renders Task 2 metric metadata rather than coercing null to zero.

- [x] Write failing tests for loading selected US ticker, missing versus zero financial values, source link/period display, stale-response rejection, manual refresh run handling, guidance append and comparison, and unsaved card navigation.
```typescript
expect(screen.getByText('未接入一致预期')).toBeInTheDocument()
expect(screen.getByRole('link', { name: /原文/ })).toHaveAttribute('href', fixtureSource)
```
- [x] Build a cohesive page: securities/coverage header; quarterly/TTM financial trends and table; profit-to-cash/working-capital and dilution; 3/6/12m price comparisons; supported valuation; industry checks with saved evidence; guidance history; filings/original sources. Render coverage/failed datasets prominently and retain dated old values.
- [x] Add guidance editor with exact period/basis/unit/source required; append a revision instead of replacing an older forecast. Expose manual industry evidence editing through the research card. Offer neutral templates for future tickers.
- [x] Add navigation entry and strategy-market selection, retain existing scores/event-study pages and A-share route. Ensure ordinary users see research meaning rather than storage internals.
- [x] Run focused UI tests, full frontend tests once, `npm run build`; fix new accessibility/type/build issues, record pre-existing warnings and commit explicit Task 5 paths.

### Task 6: Real-source collection, independent review and local delivery

**Files:** Create Task 6 owners; update plan completion markers and approved spec state; implement only integration fixes identified by evidence.

**Interfaces:** Production access uses current source checkout `.env` through dotenv inside tools; never print its contents. Verification script outputs sanitized JSON evidence to `.local/cross-market/verification.json`.

- [x] Read back baseline watchlist, existing card JSON/version, filing index and schedule configuration; store hashes/safe snapshots. Verify listener/service identity before restarting anything. Inspect deployment/start method and avoid starting duplicate schedulers.
- [x] Review Tasks 1–5 and fix Important/Critical findings through original implementers. Use whole-change diff against `00a8b2e`, not original main, to isolate this feature from prior A-share work.
- [x] Apply only migration 014 to existing DB using a transaction after inspecting it. Real-source collection runs from the primary source checkout after guarded source synchronization/build, so `.local/us-research/raw/` belongs to the deployed runtime. Preserve originals and obtain first-seen versions with notifications disabled. Verify at least MU/CRDO/NVDA fact provenance and all 8 companies' coverage; read every referenced snapshot from the production directory and verify its hash. Source failures remain visible and are retried only where actionable.
- [x] Add read-only verification script with explicit required identities and real evidence assertions. Validate snapshot preservation, data-source hashes, exact quarter calculations, raw/adjusted-price separation, no fabricated missing values and source attribution. Exercise writes such as alternate A-share/card conflict in TEMP/isolated DB only.
- [x] Run complete backend tests, explicit TEMP-table DB tests, frontend tests/build. Record count and skips. Run browser flows through supported browser tooling: A-share list/filter/select, US select/refresh/financials/source/guidance, cross-market cards, responsive layout and old pages.
- [x] Synchronize only files changed since the isolated baseline back to original checkout. Check each original file's current hash against the captured source-baseline manifest before copying; reconcile concurrent edits instead of overwriting. Exclude dependency symlinks, secrets, raw data and `.git`. Build frontend in original checkout and reload the verified existing local service; read back HTTP and use browser against `127.0.0.1:8787`.
- [x] Publish local verification results in `docs/cross-market-research.md`, link from existing docs and README. Record limitations honestly, including no strict historical snapshots and company-specific data not disclosed. Keep local commits and worktree available, no push/PR/cloud publication. Report actual local URLs and tests, with user acceptance pending.

- [x] Initial delivery completed `finishing-a-development-branch` environment detection and kept the local branch/worktree after scoped documentation approval. This historical retention choice was superseded by the user's explicit request to commit on main and clean up, recorded below.
- [x] Follow-up local integration (2026-10-16): commit `a0ab8ae` on main has the same complete source tree as reviewed `ec29eed`, including the required prior A-share foundation. Backend 526 passed / 52 skipped, frontend 129 passed and build succeeded in the main checkout. After a verified full-history Git bundle and hash checks on 200 evidence files, the application archived/removed this task's worktree and the temporary branch was deleted. Other worktrees and remote-tracking refs were unchanged; no push. Evidence: primary `.local/cross-market/main-integration/`.
- [ ] User manual acceptance (not delegated to Agent; no real US card/guidance saved for the user).

## Final evidence and limits

- Final backend: 526 passed / 52 skipped; separately inspected seven TEMP-table suites: 92 passed. Full frontend: 129 passed; worktree and primary builds: 0 errors. Existing dependency warnings remain.
- Final read-only verification: 65 source snapshots, 17,131 financial facts and 2,747 independent arithmetic checks; all eight return 12 fiscal rows. TSLA has four annual gaps under its existing 10-K exclusion; twelve rows do not establish completeness or continuity.
- Six preservation checks passed: nine watchlist records, original 29 filing identities, 688008 card v2/body/history, four schedules, notification configuration and environment file. Only migration 014 was applied. These six groups do not establish row-by-row preservation of every A-share dataset. Two read-only GETs established the 688008 bar-count difference: current default window 2025-09-11 to 2026-10-16 shows 261, while the original fixed window 2025-09-10 to 2026-10-15 returns 262; only 2025-09-10 is excluded, and stored_rows remains 262. Latest data remains 2026-10-15, so stale denotes the newly closed 2026-10-16 not yet covered. Evidence: primary `.local/cross-market/ashare-window-readback.json`; no source sync or writes. Current 8 financials / 163 announcements / 3 disclosures / card v2 are also present.
- Real NVDA direct revenue 96,221m versus cumulative difference 96,222m leaves revenue TTM null; standard cash PPE/FCF and latest common earnings remain unknown. Unverified market-cap valuations remain null.
- Browser flows completed with documented limits: native confirmation interaction timed out, drafts remained and were cleared without saving; precise confirm/cancel/unload behavior is automated-component evidence. At 390px the sidebar was collapsed and wide tables scroll internally.
- Broad review findings 1–4 closed; finding 5 remains partly open: fresh next-day quote/out-of-window historical bars can trigger a conservative old-value warning. This Minor is parked under the one-wave ruling; displayed latest values/provenance remain correct.
- Evidence and commands: [cross-market-research.md](../../v2/features/fundamentals.md); local JSON evidence under the primary `.local/cross-market/`. User acceptance remains pending.

## Coverage review

All design sections map to Tasks 1–6. Shared interfaces are defined before consumers; shared files change sequentially. `research/cards.py` is the only market-neutral card owner; A-share facade preserves old callers. The current user explicitly authorized planning through local verification, so no additional between-task approval is required.
