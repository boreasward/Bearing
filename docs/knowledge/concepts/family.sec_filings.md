---
id: family.sec_filings
title: SEC 申报
group: family
version: 1
applies_to: []
summary: "公司向 SEC 提交的 8-K / 10-Q / 10-K 列表，含受理时间（UTC）与 Item；无申报不等于缺数据。"
related: [rule.new_filing, rule.earnings_gap_up, family.news, system.cutoff]
sources: [investment_assistant/data/families.py, investment_assistant/data/collect.py, investment_assistant/analysis/rules.py, docs/archive/v2/features/sec-filings.md, docs/archive/v2/features/data-menu.md]
---

## 是什么

SEC 申报是上市公司提交给美国证监会的正式文件，系统关心三种：8-K（重大事项，其中 Item 2.02 是业绩公告）、10-Q（季报）、10-K（年报）。系统只存列表（表单类型、申报日、受理时间、Item、文件名），用来判断「出新财报了」和「财报是什么时候发的」，不在这一族里读正文。

## 怎么算 / 从哪来

- 来源是 SEC 公开接口。有两条采集路径，目标不同：
  - 注册任务 `filings`：按监控清单或配置的名单下载申报、归档并提取正文事实（营收、利润等），这是「工具 → SEC 文件」的来源；
  - 每日批处理 `workbench`：按「当前观察池 + 持仓」读取申报列表，补全缺失的受理时间和 Item，**只补空值、不覆盖**。
- 受理时间用 UTC 保存。规则按它判断财报是盘前、盘中还是盘后发布：盘后算下一个交易日，盘前算当天，盘中发布不触发财报跳空规则。仍缺受理时间的财报只能触发「新财报」。
- 「覆盖」的口径：近 120 天内有申报记录的标的数。

## 在系统里哪里看到

- 「数据」菜单的「SEC 申报」卡与详情：最近拉取、逐只覆盖；浏览区近 180 天，分开显示已入库的 `filings` 和尚未入库的 `sec_8k`，无受理时间的条目标注「申报日未知」。
- 「标的」页的 SEC 列表；「今日」里触发 [新财报](rule.new_filing.md) 或 [财报跳空向上](rule.earnings_gap_up.md) 的单元。

## 注意

- **无申报 ≠ 缺数据**：一只股票近 120 天本来就可能没有任何申报，这不是采集故障。
- 两条路径目标不同，所以「数据」菜单卡片的「所属任务」指注册任务 `filings`，而最近一次拉取行按记录如实显示实际来自哪条路径。
- Agent 读到的申报同样只限截止时间之前入库的数据。
