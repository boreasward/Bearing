---
id: family.sector
title: 行业归类
group: family
version: 1
applies_to: [fetch-1, portfolio-check-1]
summary: "用 SEC 的 SIC 与仓库映射生成行业组，允许逐标的覆盖；未归类单列提示，不凭空归组，也不因缺分类阻断计划。"
related: [account.portfolio_check, indicator.sector_strength, system.adapter_registry]
sources: [investment_assistant/data/adapters/sector.py, investment_assistant/data/sources/edgar.py, config/sic_groups.json, config/sector_overrides.json, investment_assistant/data/repo.py]
---

## 是什么

行业组是[组合检查](account.portfolio_check.md)统计集中度的分类依据，不是用行业 ETF 算出的[行业强弱](../../../../knowledge/concepts/indicator.sector_strength.md)，两者不互相替代。

## 怎么算 / 从哪来

- 读 EDGAR submissions 的 `sic` / `sicDescription`，经 `config/sic_groups.json` 的 SIC 两位映射得到组；`config/sector_overrides.json` 可按 ticker 覆盖。没有备用外部源。
- 写 `sector_classifications`；仅 SIC、说明或组答案变化时追加，不因相同答案重新抓取而刷新 `fetched_at`。读取截止前最新行，超过90天标滞后。
- daily 采用日线宽集合去掉 SPY 与行业 ETF，包含活跃研究请求；显式补采范围不扩大。
- 没有映射则保留 `group=None`，组合结果列「未归类」及 `sector_unknown`；不计入两个行业上限，不以缺分类本身拒绝，但仍遵守现金与单票/总风险限制。

## 在系统里哪里看到

「数据 → 行业归类」看 ticker、SIC、说明、行业组、source 和 fetched_at；今日与账户组合检查看行业敞口。

## 注意

行业映射粗略且人工维护，不代表公司没有跨行业风险；未知行业不能显示成零风险。计算版本为 `portfolio-check-1`，有效性仍是 Gate HOLD。
