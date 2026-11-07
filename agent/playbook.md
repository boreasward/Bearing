# 每日操作手册

开始先调 `get_agenda` 读当天待办；按它逐项完成；每项完成后再调一次 `get_agenda` 确认该项已
标记完成。待办分四类，逐类处理；处理不完的项照实留待办里，宿主会在简报里写「未完成」。

## 一、持仓复查

- **做什么**：对每只持仓回答「当初买入的理由还成立吗」。补录的持仓没有入场论点，改问
  「现在有没有明显的卖出理由」。
- **看哪些工具**：`get_positions`、`get_price_bars`、`get_indicators`、`get_news`、`get_filings`、
  `get_earnings`、`get_fundamentals`。
- **结论怎么保存**：`save_holding_review`，verdict 三选一——`holds`（成立）/ `broken`（不成立，
  宿主会转成「下次开盘卖出，原因：论点失效」）/ `unknown`（无法判断，写明缺什么）。
  附理由与反证。

## 二、再次上榜的待入场计划

- **做什么**：对当天再次上榜的待入场计划重新确认原论点。
- **看哪些工具**：上面那些，另加 `get_plans` 读原计划与变化记录。
- **结论怎么保存**，三选一：
  - 数字照旧、有效期延续：`continue_plan`；
  - 要改价位 / 论点 / 作废条件 / 最长持有：`revise_plan`，写明改了什么和原因；
  - 否决或无法判断：`save_verdict` 存 `veto` 或 `unknown`（veto 会让宿主把该计划作废）。

## 三、新的二次确认

- **做什么**：对 `get_agenda` 列出的候选按顺序做二次确认，直到名额填满或候选都有了结论。
  名额已满时只处理 `get_agenda` 给出的替换候选，不自己挑替换对象。
- **看哪些工具**：先 `compute_plan_levels` 看宿主算的价位与股数，再看 `get_price_bars`、
  `get_indicators`、`get_news`、`get_filings`、`get_earnings`、`get_fundamentals`，找反证
  （消息驱动的假突破、临近财报、成交量异常等）。
- **结论怎么保存**：`save_verdict` 存 `confirm` / `veto` / `unknown`（confirm 与 veto 必须引用
  看过的工具调用）；confirm 后用 `save_plan` 保存计划——价位缺省取宿主规则价位，股数由宿主
  计算，**不要自带止损价、卖出价或股数**。

## 四、一句话总结

- **做什么**：概括当天名单、计划和持仓的变化；没有变化就写没有变化。
- **怎么保存**：`save_summary`，1–300 字，不引入名单外的股票。

## 纪律

- 不给出也不修改止损价、卖出价和股数，只引用工具返回的值。
- 每个判断给理由和反证；数据不够就存 `unknown`，写明缺什么，不要硬给结论。
- 全部用中文。
