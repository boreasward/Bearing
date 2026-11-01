# 美股财季数据与指引（基本面）

> 「标的」页的基本面一栏，v2.1.0 由 v1 的美股研究数据页并入（研究观察卡已删除，旧实现见 tag `v2.0.0`）。计算口径与来源设计见[归档 spec](../archive/roadmap-v1/specs/2026-10-16-cross-market-medium-term-research-design.md)。

## 功能

- **美股财季数据**：规范化与原文归档在 `data/`，财季计算在 `derivation/indicators/fiscal.py`，读取与指引用例在 `analysis/fundamentals.py`。SEC Company Facts 和申报原文中的收入、经营现金流、资本开支、利润等数值保留申报版本、来源 URL、JSON 定位和原始快照的 SHA-256，只追加到 `us_research_observations`。行情读取已入库 `price_bars`，最后日收盘作为报价并明确标注口径；读取不发起在线采集。
- **计算口径**：实际财季按起止日对齐，不直接用申报的 `fy/fp` 或日历季度。单季金额优先用直接披露的值，只有在同财年、同币种、同口径时才用累计值差分；TTM 需要连续四季；标准 FCF = CFO − 现金 PPE。缺少输入时保持 null 并写明原因，不用相近的标签替代。估值（PE / PS / FCF 收益率）在市值来源核实之前保持为空。
- **公司指引**：按原文录入，修订时追加新版本、保留原始记录；只在口径完全一致时与实际值比较。

## 入口与验证

- 页面：「标的 → 基本面」（财季表、来源展开、指引编辑）；标的选择跟随「标的」页，没有独立的选股入口。旧同步入口已退役，返回 410 `use_data_menu`，手动采集统一从「数据」菜单进入。SEC 成功归档后的本地研究刷新保留。基本面读取失败只影响本栏，K 线图、时间线与持仓照常显示。
- API：

  ```bash
  curl -s "localhost:8787/api/us-research/MU"
  curl -s "localhost:8787/api/us-research/MU/guidance"
  ```

- 只读验收脚本（只读事务 + 回环 GET，唯一的写入是 `--output` 指定的报告）：

  ```bash
  .venv/bin/python scripts/verify_cross_market.py --api-url http://127.0.0.1:8787 --raw-root .local/us-research/raw --output .local/cross-market/verification.json
  ```

## 已知限制

- Company Facts 不含分部、产品和经营指标；HBM、RPO、客户集中度这类数据需要原始披露或人工附证。
- TSLA 沿用「不收 10-K」的规则，所以第四季有缺口，不能把它的 12 行当作连续 12 季，也不能跨缺口算 TTM。
- 当前供应商的复权回填，不等于当时的拆股或分红版本，也不保证是严格的总回报。
