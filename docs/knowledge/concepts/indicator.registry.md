---
id: indicator.registry
title: 标准指标注册表
group: indicator
version: 1
applies_to: [indicator-1, plan-2, screen-1, regime-2, rules-1]
summary: "22个注册项包装既有计算，按截止日现算、不落表；窗口是所需日线根数，保留 close/adj_close 均线和 SPY/QQQ 相强差异。"
related: [indicator.atr14, indicator.moving_average, indicator.relative_strength, indicator.screen_rs, indicator.breadth, indicator.sector_strength]
sources: [investment_assistant/derivation/indicators/registry.py, investment_assistant/derivation/indicators/_price_math.py, investment_assistant/derivation/indicators/screen_metrics.py, investment_assistant/derivation/indicators/breadth.py]
---

## 是什么

`indicator-1` 把已有公式登记为 `id/title/basis/window/inputs/fn/version/concept/used_by`。它不采集数据、不落指标表、不修改已有数值或舍入；原数据族 ATR 已并入[ATR14](indicator.atr14.md)。

## 怎么算 / 从哪来

全部22项 `inputs=('bars',)`；`window` 是读取根数，不是名字里的周期。按 `price_date ≤ session` 排序、取末尾窗口，基准同样过滤；日线已由调用者按入库截止时间读取。样本/基准不足返回空，不补零。

| id | basis | window（根） | used_by / 口径 |
| --- | --- | --- | --- |
| `atr14` | high_low | 15 | plan-2、screen-1、agent-tools |
| `ma20_close` | close | 20 | agent-tools |
| `ma50_close` | close | 50 | agent-tools |
| `ma200_close` | close | 200 | agent-tools |
| `ma20_adj_close` | adj_close | 20 | rules-v1 |
| `ma50_adj_close` | adj_close | 50 | rules-v1、screen-1 |
| `ma200_adj_close` | adj_close | 200 | rules-v1、screen-1 |
| `high_20` | high_low | 20 | agent-tools，含当日 |
| `low_20` | high_low | 20 | agent-tools，含当日 |
| `high_252` | high_low | 252 | agent-tools，含当日 |
| `low_252` | high_low | 252 | agent-tools，含当日 |
| `avg_volume_20` | volume | 20 | agent-tools，含当日 |
| `volume_ratio` | volume | 20 | agent-tools，当前量 / 含当日20日均量 |
| `rs20` | adj_close | 21 | agent-tools，相对 SPY |
| `rs60` | adj_close | 61 | agent-tools，相对 SPY |
| `screen_rs` | adj_close | 61 | screen-1，½(r20−QQQ r20)+½(r60−QQQ r60) |
| `ret20` | adj_close | 21 | screen-1 |
| `ret60` | adj_close | 61 | screen-1 |
| `dollar_volume_20` | volume | 20 | screen-1，close×volume |
| `atr_pct` | high_low | 15 | screen-1，ATR14 / close |
| `breadth` | breadth | None | regime-2，各标的/指标独立判资格 |
| `sector_strength` | adj_close | 61 | regime-2，相对 SPY |

两套[均线](indicator.moving_average.md)不统一：Agent 用拆股调整 `close`，规则/筛选用全复权 `adj_close`。`rs20/rs60` 与 `sector_strength` 用 SPY；`screen_rs` 用 QQQ，调用者须分别供基准，不能替换成同一个。`rules-v1` 是 used_by 标签，规则版本仍为 `rules-1`。

`get_indicators` 保持旧 dict 的键、顺序与数值：close 均线仍叫 `ma20/ma50/ma200`，`last_close` 是日线字段，不是第23项。单项工具 `get_indicator` 返回 id、basis、window、value、computed_through、version。独立推导重算仍用自己的常量与计算，不调用注册表 fn。

## 在系统里哪里看到

「数据 → 指标」列出全部注册项、窗口与使用方；计划推导与 Agent 工具结果可对应具体指标 id。

## 注意

广度没有统一最小窗口，`None` 不等于无需样本：MA50/MA200/52周分别要求50/200/252根，并排除截止日无日线的标的。计算一致只证明口径保持，所有规则、计划和 Agent 继续 Gate HOLD。
