from datetime import date
from decimal import Decimal, InvalidOperation

from investment_assistant.core.texts import PORTFOLIO_REASON_TEXT
from investment_assistant.notify.message import Notification, NotifyField
from investment_assistant.analysis.regime import LABEL_TEXT

MAX_FIELD = 1024
MAX_MESSAGE = 6000
ACTIONS = {'buy': '买入', 'sell': '卖出', 'watch': '观察'}
SOURCE_NAMES = {'bars': '日线', 'news': '资讯', 'earnings': '财报日期', 'sec': 'SEC', 'etf_bars': 'ETF 日线',
                'agent': 'Agent',
                'fred': 'FRED', 'regime': '区制'}
_SECTOR_STATE = {'strong': '强', 'weak': '弱', 'mixed': '分化'}


def _num(value) -> str:
    """A stored NUMERIC without its padding zeros: 250.770000 -> 250.77, 94.000000 -> 94."""
    if value is None:
        return '—'
    try:
        return format(Decimal(str(value)).normalize(), 'f')
    except InvalidOperation:
        return str(value)


def _fred_missing_text(market: dict) -> str:
    """Why no FRED value is shown: no key, a failed fetch, or simply no stored rows."""
    status = list(((market.get('inputs') or {}).get('macro_status') or {}).values())
    if status and all(s == 'not_configured' for s in status):
        return 'FRED 未配置'
    if 'error' in status:
        return 'FRED 采集失败（见异常段）'
    return 'FRED 无数据'


def _market_environment(market: dict | None) -> str:
    """One line block for the overview market field; absent snapshot or FRED says so plainly."""
    if not market:
        return '市场环境未生成'
    breadth = market.get('breadth') or {}
    lines = []
    label = LABEL_TEXT.get(market.get('label'))
    head = (f"区制 {label} · 站上 MA50 {breadth['pct_50']:.1%} · MA200 {breadth['pct_200']:.1%}"
            f" · 新高 {breadth.get('new_highs', 0)} / 新低 {breadth.get('new_lows', 0)}"
            if label is not None and breadth.get('pct_50') is not None and breadth.get('pct_200') is not None
            else f"区制 {label or '未知'}")
    lines.append(head)
    sectors = market.get('sectors') or {}
    if sectors:
        parts = []
        for etf, view in sectors.items():
            if view is None:
                parts.append(f"{etf} 数据不足")
            else:
                parts.append(f"{etf} {view['rs20']:+.1%} {_SECTOR_STATE.get(view.get('state'), '数据不足')}")
        lines.append(' · '.join(parts))
    macro = market.get('macro') or {}
    if not any(view is not None for view in macro.values()):
        lines.append(_fred_missing_text(market))
    else:
        hy, curve = macro.get('BAMLH0A0HYM2'), macro.get('T10Y2Y')
        parts = []
        for name, view in (('HY OAS', hy), ('2s10s', curve)):
            if view is None:
                parts.append(f'{name} 无数据')
            else:
                # JSONB round-trips the FRED Decimals as strings; format numerically either way.
                change = f"{float(view['change']):+}" if view.get('change') is not None else '无变化'
                parts.append(f"{name} {_num(view['value'])}（20 期 {change}）")
        lines.append(' · '.join(parts))
    return '\n'.join(lines)


def _clip_field(fields: list[NotifyField], name: str, value: str) -> None:
    if len(value) > MAX_FIELD:
        value = value[:MAX_FIELD - 1] + '…'
    fields.append(NotifyField(name, value))


def _fit_message(title: str, fields: list[NotifyField], footer: str) -> Notification:
    """Drop whole fields until the message fits Discord's 6000 chars; note what was dropped."""
    dropped = 0
    while fields and (len(title) + sum(len(f.name) + len(f.value) for f in fields) + len(footer)) > MAX_MESSAGE:
        fields.pop()
        dropped += 1
    if dropped:
        fields.append(NotifyField('溢出', f'其余 {dropped} 条见系统'))
    return Notification(title=title, color=5198940, fields=fields, footer=footer)


def date_label_for(start: date | None, end: date | None) -> str:
    if start is None:
        return date.today().isoformat()
    if end is None or end == start:
        return start.isoformat()
    return f"{start.isoformat()} ~ {end.isoformat()}"


def _footer() -> str:
    return f"earning-agent • {date.today().isoformat()}"


def filings_digest(summary: dict, *, date_label: str | None = None) -> Notification:
    label = date_label or date.today().isoformat()
    filings = summary.get("filings", [])
    rows = "\n".join(
        f"• **{f.get('ticker')}** {f.get('form')} — {f.get('filing_date') or f.get('filed_at', '')}"
        for f in filings[:15]
    ) or "该日期无新财报"
    fields = [NotifyField(f"新提交 ({summary.get('downloaded_count', 0)})", rows)]
    return Notification(title=f"📄 财报 · {label}", color=15844367, fields=fields, footer=_footer())


def workbench_digest(digest: dict) -> list[Notification]:
    """Overview, new plans, plan updates (when any), holdings, pending/errors, screen report: one
    message each (design §7.2, v2.5 §10.3)."""
    session = digest.get('session', '')
    overview = digest.get('overview', {})
    lines = [f"交易日 {session}", f"新计划 {overview.get('new_plans', 0)} · 持仓 {overview.get('holdings', 0)}",
             f"待处理 {overview.get('pending', 0)} · 异常 {overview.get('errors', 0)}"]
    if overview.get('extended_plans'):
        lines.append(f"扩展池新计划 {overview['extended_plans']} 份（不推送）")
    agent = overview.get('agent') or {}
    if agent:
        lines.append(f"Agent 建议 {agent.get('ok', 0)} · 不建议 {agent.get('pass', 0)}"
                     f" · 未给出 {agent.get('missing', 0)}")
    if overview.get('agent_quota_exhausted'):
        lines.append('⚠️ Agent 额度用尽，其余单元未运行')
    updates = overview.get('plan_updates')
    if updates is not None:
        lines.append(f"计划更新 修订 {updates.get('revise', 0)} · 作废 {updates.get('invalidate', 0)}")
    if overview.get('core_empty'):
        lines.append('核心池为空，只推送持仓')
    if overview.get('last_push_failed'):
        lines.append('⚠️ 上次推送失败，请核对')
    retro = overview.get('retro') or {}
    if retro.get('new_cards') or retro.get('to_review'):
        lines.append(f"复盘卡 上次推送后新增 {retro.get('new_cards', 0)} · 待审定 {retro.get('to_review', 0)}")
    degraded = overview.get('fetch_degraded') or []
    if degraded:
        lines.append('数据：' + '；'.join(f"{f['title']} {f['ok_count']}/{f['target_count']}"
                                          f"（{f['missed']} 个目标没拿到，见运行详情）" for f in degraded[:3]))
    overview_fields = [NotifyField('概览', '\n'.join(lines)),
                       NotifyField('市场环境', _market_environment(overview.get('market')))]
    overview_msg = _fit_message(f"📊 工作台 · {session}", overview_fields, _footer())

    fields: list[NotifyField] = []
    plans = digest.get('plans') or []
    if not plans:
        _clip_field(fields, '新计划', '今日无新计划')
    for p in plans:
        sizing = '缺资金快照，无法计算' if p.get('sizing_status') == 'no_equity' else (
            f"{p.get('shares')} 股 · 风险 {_num(p.get('risk_amount'))}")
        funding = f"资金日 {p['snapshot_as_of']}" if p.get('snapshot_as_of') else None
        notes = '；'.join([*(f"提示 {w}" for w in p.get('warnings', [])),
                           *(f"作废 {r}" for r in p.get('invalid_reasons', []))])
        head = f"动作 {ACTIONS.get(p.get('action'), p.get('action'))} · {'、'.join(p.get('rule_ids') or [])} · {p.get('plan_version')}"
        sector = p.get('sector')
        sector_text = (f"行业 {sector['etf']} {_SECTOR_STATE.get(sector.get('state'), '数据不足')}"
                       if sector else '行业 未映射')
        context = f"区制 {LABEL_TEXT.get(p.get('regime_label'), '未知')} · 下次财报 {p.get('next_earnings') or '未知'} · {sector_text}"
        band = (f"区间 {_num(p['entry_low'])}–{_num(p['entry_high'])} · 止损 {_num(p['stop'])}"
                f" · T1 {_num(p['target1'])}"
                if p.get('entry_low') is not None else None)   # agent-only units have no rule band
        field_name = f"【筛选】{p['ticker']}" if p.get('source') == 'screen' else p['ticker']
        agent = p.get('agent')
        own = _baseline_view(p)       # plan-2 rule baseline as a plan of its own (v2.9 §11, D13)
        agent_plan = bool(agent) and agent.get('status') == 'ok' and bool(agent.get('entry_type')) \
            and not agent.get('legacy')
        if own is not None and not agent_plan:
            # no executable Agent plan today (none, pass, error, skipped, old version): the baseline is the plan,
            # in the same three lines; the Agent's explanation follows the third line
            lines = _executable_lines(own, p['ticker'])
            explanation = _agent_line(agent, ticker=p['ticker']) if agent else ''
            lead = ['\n'.join(filter(None, [lines, explanation])), *_merged_lines(p.get('merged'))]
            position = f"仓位 {sizing}" if p.get('sizing_status') is not None else None
            band = None
        elif agent:
            # v2.5 §10.3: the agent line first, the rule plan after it as the baseline.
            # 「规则基线：区间 · 止损 · T1 · 股数」 (§10.3, verbatim): shares only, no risk amount.
            shares = None if p.get('sizing_status') is None else (
                '缺资金快照，无法计算' if p['sizing_status'] == 'no_equity' else f"{p.get('shares')} 股")
            baseline = ' · '.join(filter(None, [band, shares]))
            lead = [_agent_line(agent, ticker=p['ticker']), f"规则基线：{baseline}" if band else None,
                    *_merged_lines(p.get('merged'))]
            position = None
        else:
            lead = []
            position = f"仓位 {sizing}" if p.get('sizing_status') is not None else None
        _clip_field(fields, field_name, '；'.join(filter(None, [
            *lead,
            head,
            context,
            None if (agent or own is not None) else band,
            position, funding, f"有效期 至 {p['valid_until']}", notes, p.get('evidence', ''),
            p.get('brief_summary') or '', p.get('contra') or '', p.get('url', '')])) or '—')
    plans_msg = _fit_message(f"🆕 新计划 · {session}", fields, _footer())

    fields = []
    for h in digest.get('holdings') or []:
        eve = '⚠️ 下一交易日为财报日' if h.get('earnings_eve') else ''
        alerts = ''
        if 'plan_stop' in (h.get('plan_alerts') or []):
            alerts = '⚠️ 今日触发 plan_stop，请核对券商止损单是否已成交'
        elif 'plan_target' in (h.get('plan_alerts') or []):
            alerts = '今日触发 plan_target（卖出一半）'
        if h.get('instruction'):         # v2.9 §11: one status line per position, quiet days too; exit gets a ⚠
            _clip_field(fields, h['ticker'], '；'.join(filter(None, [_holding_status_line(h), eve, alerts])))
            continue
        stop_line = f"止损 {h.get('stop')}（{h.get('mode')}）" if h.get('stop') is not None else '无计划持仓（position_drawdown）'
        t1 = '已触 T1' if h.get('t1_reached') else '未触 T1'
        pct = ''
        if h.get('to_stop_pct') is not None:
            pct = f"距止损 {h['to_stop_pct']:+.1%} · 距 T1 {h.get('to_target_pct', 0):+.1%}"
        stale = '今日持仓状态未写入（每日复查失败），以下为现算' if h.get('stale') else ''
        _clip_field(fields, h['ticker'], '；'.join(filter(None, [
            f"{h.get('quantity')} 股 @ {h.get('avg_cost')}", stop_line, t1, pct, eve, alerts, stale])) or '—')
    holdings_msg = _fit_message(f"💼 持仓 · {session}", fields, _footer())
    updates_msg = _plan_updates_message(session, digest.get('plan_updates') or [])

    fields = []
    pending = digest.get('pending') or {}
    entry, holding, exiting = pending.get('entry') or [], pending.get('holding') or [], pending.get('exit') or []
    _clip_field(fields, f"入场期可执行 {len(entry)} · 持仓 {len(holding)}（退出指令 {len(exiting)}）",
                '\n'.join(filter(None, [f"入场期：{'、'.join(entry)}" if entry else '',
                                        f"持仓：{'、'.join(holding)}" if holding else '',
                                        f"退出指令：{'、'.join(exiting)}" if exiting else ''])) or '无')
    expiring = pending.get('expiring') or []
    _clip_field(fields, f"即将过期计划 ({len(expiring)})",
                '\n'.join(f"{e['ticker']} 至 {e['valid_until']}" for e in expiring) or '无')
    errors = digest.get('errors') or []
    _clip_field(fields, f"异常 ({len(errors)})", '\n'.join(errors) or '无')
    for source, items in (digest.get('errors_by_source') or {}).items():
        if items:
            _clip_field(fields, SOURCE_NAMES.get(source, source), '\n'.join(str(i) for i in items))
    pending_msg = _fit_message(f"⏳ 待处理与异常 · {session}", fields, _footer())
    portfolio_msg = _portfolio_message(session, digest.get('portfolio'))
    return [overview_msg, plans_msg, *([portfolio_msg] if portfolio_msg else []),
            *([updates_msg] if updates_msg else []), holdings_msg, pending_msg,
            _screen_message(session, digest.get('screen'))]


def _portfolio_message(session: str, portfolio: dict | None) -> Notification | None:
    """「🧩 组合检查」 (v2.14 §7.4), right after the new plans: how many plans can be done together, how many the
    check blocked and why, the order and one sentence of reason. None without a hard result — silence is not an
    all-clear, the overview already says how many new plans there are."""
    if not portfolio:
        return None
    reasons = PORTFOLIO_REASON_TEXT
    head = f"可一起做 {portfolio.get('ok', 0)} 条 · 被拦 {portfolio.get('blocked', 0)} 条"
    if portfolio.get('deferred'):
        head += f" · 因现金暂缓 {portfolio['deferred']} 条"
    fields = [NotifyField('结果', head)]
    blocked = portfolio.get('blocked_items') or []
    if blocked:
        _clip_field(fields, f"被拦 ({len(blocked)})",
                    '\n'.join(f"{b['ticker']}：" + ('、'.join(reasons.get(r, r) for r in b.get('reasons') or []) or '—')
                              for b in blocked))
    order = portfolio.get('order') or []
    if order:
        _clip_field(fields, '建议先后', ' → '.join(order))
    if portfolio.get('summary'):
        _clip_field(fields, '一句理由', '；'.join(filter(None, [portfolio['summary'], portfolio.get('first_reason')])))
    elif portfolio.get('status') == 'degraded':
        _clip_field(fields, '一句理由', 'Agent 不可用，只有硬结果')
    return _fit_message(f"🧩 组合检查 · {session}", fields, _footer())


def _plan_update_text(u: dict) -> str:
    """One revision or invalidation of the plan-updates message (v2.5 §10.3, wording verbatim; v2.9 §11: a
    holding-phase invalidation is an exit instruction, a retrigger revision says 「再次触发」)."""
    if u.get('kind') == 'invalidate':
        if u.get('mode') == 'holding':
            return f"{u['ticker']} 退出指令（{u.get('reason') or '—'}）"
        by = 'Agent' if u.get('decided_by') == 'agent' else '系统规则'
        return f"{u['ticker']} 作废（{by}）：{u.get('reason') or '—'}"
    changes = '；'.join(str(c) for c in u.get('changes') or [])
    levels = u.get('levels') or {}
    if u.get('via') == 'retrigger':
        if not changes:
            changes = f"{_retrigger_phrase(u.get('retrigger'))}，沿用价位，有效期延至 {u.get('valid_until') or '—'}"
        return (f"{u['ticker']} 第 {u.get('from_version')} → {u.get('to_version')} 版（再次触发）：{changes}"
                f" · 新价位 区间 {_num(levels.get('entry_low'))}–{_num(levels.get('entry_high'))}"
                f" · 止损 {_num(levels.get('stop'))} · T1 {_num(levels.get('target1'))}"
                f" · 有效期至 {u.get('valid_until') or '—'}")
    head = f"{u['ticker']} 第 {u.get('from_version')} → {u.get('to_version')} 版"
    return (f"{head}{'：' + changes if changes else ''} · 新价位 区间 {_num(levels.get('entry_low'))}"
            f"–{_num(levels.get('entry_high'))} · 止损 {_num(levels.get('stop'))} · T1 {_num(levels.get('target1'))}"
            f" · 有效期至 {u.get('valid_until') or '—'}")


def _retrigger_phrase(retrigger: dict | None) -> str:
    """「再次触发（<规则>，<信号日>）」 from a review row's ``detail.retrigger`` (the newest merge)."""
    from investment_assistant.core import texts as labels

    retrigger = retrigger or {}
    rules = '、'.join(labels.RULE_TEXT.get(r, r) for r in retrigger.get('rule_ids') or [])
    days = retrigger.get('unit_signal_dates') or []
    return f"再次触发（{rules}，{days[-1] if days else '—'}）"


def _signed(value, digits: int = 2, suffix: str = '') -> str:
    """+8.10% / −5.20% (a real minus sign, spec §7.3's example) from a stored number."""
    number = float(value)
    return f"{number:+.{digits}f}{suffix}".replace('-', '−')


def _holding_status_line(h: dict) -> str:
    """「MU 持有 4/10 天 · 距止损 −5.20% · 距 T1 +8.10% · 现 +0.6R · 继续持有：<维持理由>」 (spec §7.3, §11); an exit
    instruction gets a ⚠ in front. Quiet days are pushed too."""
    from investment_assistant.execution import timeline

    held = f"持有 {h['held_sessions']}/{h['max_hold_sessions']} 天" if h.get('max_hold_sessions') \
        else f"持有 {h['held_sessions']} 天"
    parts = [f"{h['ticker']} {held}"]
    if h.get('to_stop_pct') is not None:
        parts.append(f"距止损 {_signed(h['to_stop_pct'], 2, '%')}")
    if h.get('to_t1_pct') is not None:
        parts.append(f"距 T1 {_signed(h['to_t1_pct'], 2, '%')}")
    elif h.get('t1_reached'):
        parts.append('已触 T1')
    if h.get('r_now') is not None:
        parts.append(f"现 {_signed(h['r_now'], 1, 'R')}")
    line = f"{' · '.join(parts)} · {timeline.INSTRUCTION_TEXT[h['instruction']]}：{h.get('reason') or '—'}"
    return f"⚠ {line}" if h['instruction'] == 'exit' else line


def _plan_updates_message(session: str, updates: list[dict]) -> Notification | None:
    """「🔁 计划更新」: one field per revision / invalidation; no message on a day without any."""
    if not updates:
        return None
    fields: list[NotifyField] = []
    for u in updates:
        _clip_field(fields, '作废' if u.get('kind') == 'invalidate' else '修订', _plan_update_text(u))
    return _fit_message(f"🔁 计划更新 · {session}", fields, _footer())


def _rs(value) -> str:
    return '—' if value is None else f"{float(value):.3f}"


def _pct(value) -> str:
    """ATR / close as a percentage, like the page: 0.0456 -> 4.56%."""
    return '—' if value is None else f"{float(value) * 100:.2f}%"


def _screen_shares(plan: dict) -> str:
    """Suggested size of a screen trigger's plan; no shares without an equity snapshot."""
    if plan.get('shares') is not None:
        return f"建议 {plan['shares']} 股"
    return '缺资金快照，无法计算股数' if plan.get('sizing_status') == 'no_equity' else '无建议股数'


def _screen_message(session: str, screen: dict | None):
    """The fifth message: the daily screen report (§9.1)."""
    fields: list[NotifyField] = []
    if screen is None:
        _clip_field(fields, '概览', '今日无筛选记录')
        return _fit_message(f"🔎 筛选报告 · {session}", fields, _footer())
    if screen.get('status') == 'error':
        _clip_field(fields, '概览',
                    f"筛选未运行：{screen.get('error')}\n沿用成员 {screen.get('members', 0)} 只")
    else:
        counts = screen.get('counts') or {}
        selected = screen.get('selected') or []
        trigger_picked = sum(1 for t in screen.get('triggers') or [] if t.get('ticker') in selected)
        lines = [f"{screen.get('calc_version')} · 范围 {counts.get('universe', 0)} · "
                 f"通过过滤 {counts.get('trigger', 0) + counts.get('setup', 0)}",
                 f"今日入选 {len(selected)}（触发 {trigger_picked} / 形态 {len(selected) - trigger_picked}）· "
                 f"成员 {screen.get('members', 0)}/{screen.get('cap', 0)} · 移出 {len(screen.get('removed') or [])}"]
        if screen.get('flags'):
            lines.append('提示 ' + '、'.join(str(f) for f in screen['flags']))
        _clip_field(fields, '概览', '\n'.join(lines))
    trigger_lines = []
    for t in screen.get('triggers') or []:
        plan = t.get('plan') or {}
        if t.get('review') == 'plan':
            outcome = (f"入场 {_num(plan.get('entry_low'))}–{_num(plan.get('entry_high'))}"
                       f" · 止损 {_num(plan.get('stop'))} · T1 {_num(plan.get('target1'))}"
                       f" · {_screen_shares(plan)}")
        elif t.get('review') == 'invalid':
            outcome = '作废 ' + '、'.join(str(r) for r in t.get('invalid_reasons') or [])
        else:
            outcome = {'conflict': '冲突 / 回避', 'watch': '等待触发'}.get(t.get('review'), t.get('review', ''))
        trigger_lines.append(f"{t.get('ticker')} · {'、'.join(t.get('rule_ids') or [])} · RS {_rs(t.get('rs'))}"
                             f" · {outcome}")
    _clip_field(fields, '今日触发 → 复核', '\n'.join(trigger_lines) or '今日无触发')
    setup_lines = [f"{s.get('ticker')} · RS {_rs(s.get('rs'))} · ATR {_pct(s.get('atr_pct'))}"
                   for s in screen.get('setups') or []]
    if screen.get('setups_more'):
        setup_lines.append(f"另 {screen['setups_more']} 只见页面")
    _clip_field(fields, '形态候选（等待触发）', '\n'.join(setup_lines) or '今日无形态候选')
    return _fit_message(f"🔎 筛选报告 · {screen.get('session', session)}", fields, _footer())


DAILY_FAILED_COLOR = 15548997      # red


def daily_failed(payload: dict) -> list[Notification]:
    """「每日工作流失败」(v2.8 §13.2): the failed node, the redacted reason, how many nodes did not run, until when the
    run can be resumed, and — with ``workbench.public_url`` — the link to its detail page.

    ``payload``: ``session``, ``node_title``, ``reason``, ``skipped`` (nodes after the failed one), ``deadline_text``
    (Beijing time), ``run_id``, ``public_url`` (may be empty); ``retried`` (default True) says whether the node was retried.
    ``skipped`` is left out when absent or 0; ``deadline_text`` when absent — a run that never started a graph, or one
    whose chain ran to the end with a stage's own ``failed`` row, cannot be resumed."""
    retried = '（已自动重试 1 次）' if payload.get('retried', True) else ''
    fields = [NotifyField('失败节点', f"{payload.get('node_title')}{retried}"),
              NotifyField('原因', str(payload.get('reason') or '—')[:300])]
    if payload.get('skipped'):
        fields.append(NotifyField('未执行', f"其后 {payload['skipped']} 个节点"))
    if payload.get('deadline_text'):
        fields.append(NotifyField('续跑', f"{payload['deadline_text']} 之前可在运行详情「从此节点继续」"))
    public_url = str(payload.get('public_url') or '').rstrip('/')
    if public_url and payload.get('run_id'):
        fields.append(NotifyField('运行详情', f"{public_url}/#tools/runs/{payload['run_id']}"))
    return [Notification(title=f"⚠️ 每日工作流失败 · {payload.get('session')}", color=DAILY_FAILED_COLOR,
                         fields=fields, footer=_footer())]


def agent_not_connected(payload: dict | None = None) -> list[Notification]:
    """「ChatGPT 连接已失效」(v2.8 §13.2): sent once per failure episode by the ``daily`` task when this execution's Agent
    nodes were skipped for ``not_connected``. The text is fixed; ``payload`` is accepted for symmetry with the other
    templates and carries nothing the notice shows."""
    return [Notification(title='⚠️ ChatGPT 连接已失效', color=DAILY_FAILED_COLOR,
                         fields=[NotifyField('影响', '今天的 Agent 建议与复查已跳过'),
                                 NotifyField('处理', '到「设置 → Agent 连接」重新连接')],
                         footer=_footer())]


def workbench_closed(ny_date) -> list[Notification]:
    return [Notification(title=f"🛌 纽约休市，未运行 · {ny_date.isoformat()}", color=9807270,
                         fields=[], footer=_footer())]


def _merged_lines(merged: list[dict] | None) -> list[str]:
    """The rule baselines of units merged into the chain today (v2.9 §4.3, §11), after the Agent lines."""
    lines = []
    for item in merged or []:
        plan = item.get('plan') or {}
        if plan.get('entry_low') is None:
            continue
        shares = '缺资金快照，无法计算' if (plan.get('sizing') or {}).get('status') == 'no_equity' \
            else f"{(plan.get('sizing') or {}).get('shares')} 股"
        lines.append(f"今日再次触发的规则基线（{item.get('signal_date')}）：区间 {_num(plan['entry_low'])}–"
                     f"{_num(plan['entry_high'])} · 止损 {_num(plan['stop'])} · T1 {_num(plan['target1'])} · {shares}")
    return lines


def _baseline_view(p: dict) -> dict | None:
    """A ``plan-2`` rule baseline in the shape ``_executable_lines`` reads (label 「规则基线」, the rules' names where
    an Agent plan has its summary); None for a plan without the execution fields (``plan-1``) or without a band."""
    from investment_assistant.core import texts as labels

    if p.get('plan_version') != 'plan-2' or p.get('entry_low') is None or not p.get('entry_type'):
        return None
    return {'label': '规则基线', 'entry_type': p['entry_type'], 'entry_low': p['entry_low'],
            'entry_high': p['entry_high'], 'stop': p['stop'], 'target1': p['target1'],
            'shares': None if p.get('sizing_status') == 'no_equity' else p.get('shares'),
            'valid_from': p.get('valid_from'), 'valid_until': p.get('valid_until'),
            'max_hold_sessions': p.get('max_hold_sessions'), 'first_counter': None, 'invalid_reasons': [],
            'summary': '、'.join(labels.RULE_TEXT.get(r, r) for r in p.get('rule_ids') or []) or '规则基线'}


def _executable_lines(agent: dict, ticker: str | None) -> str:
    """The three lines of an agent-3 version (v2.9 spec §11): the plan, the open scenarios, the max hold with the
    summary and the first counter."""
    from investment_assistant.execution import entry_rules

    shares = (f"{agent['shares']} 股" if agent.get('shares') is not None else '缺资金快照，无法计算')
    low, high, stop = _num(agent['entry_low']), _num(agent['entry_high']), _num(agent['stop'])
    version = agent.get('label') or (f"{agent['chain_signal_date']:%m-%d} 第 {agent['version_no']} 版"
                                     if agent.get('chain_signal_date') else f"第 {agent.get('version_no')} 版")
    mark = ''
    if agent.get('retrigger'):
        mark = f"（再次触发，第 {agent['retrigger']['from_version']} → {agent['retrigger']['to_version']} 版）"
    first = (f"{ticker + ' ' if ticker else ''}{version}{mark}："
             f"{entry_rules.ENTRY_TYPE_TEXT.get(agent['entry_type'], agent['entry_type'])} {low}–{high}"
             f" · 止损 {stop} · T1 {_num(agent['target1'])} · {shares} · 有效至 {agent.get('valid_until') or '—'}")
    if agent['entry_type'] == 'next_open':
        valid_from = agent.get('valid_from')
        day = f"{valid_from:%m-%d}" if hasattr(valid_from, 'strftime') else (valid_from or '首个有效日')
        second = f"开盘：仅 {day} 开盘在 {low}–{high} 买，否则作废"
    else:
        second = f"开盘：区间内买 / 高开不追挂 {high} / 低开 ≤{stop} 作废、否则回到 {low} 买"
    third = f"最长持有 {agent['max_hold_sessions']} 天未到 1R 即退出 · {agent['summary']}"
    if agent.get('first_counter'):
        third += f" · 反证：{agent['first_counter']}"
    voided = '；'.join(f"作废 {r}" for r in agent.get('invalid_reasons') or [])
    return '\n'.join([first, second, third + (f"；{voided}" if voided else '')])


def _agent_line(agent: dict | None, ticker: str | None = None) -> str:
    """The agent's line ahead of the rule baseline. agent-2: 「Agent 第 n 版：区间 · 止损 · T1 · 股数 · 摘要
    · 首条反证」 (v2.5 §10.3); agent-1 units keep their P3 §10.2 line, marked as the old version."""
    if not agent:
        return ''
    from investment_assistant.analysis.agent import REASON_TEXT
    legacy = agent.get('legacy')
    if agent['status'] == 'ok' and not legacy and agent.get('chain_state') == 'invalidated':
        return f"Agent 已作废此计划（第 {agent.get('version_no')} 版）：{agent.get('invalidated_reason') or '—'}"
    if agent['status'] == 'ok':
        shares = (f"{agent['shares']} 股" if agent.get('shares') is not None
                  else '缺资金快照，无法计算')   # same wording as the rule plan line
        band = (f"区间 {_num(agent['entry_low'])}–{_num(agent['entry_high'])}"
                f" · 止损 {_num(agent['stop'])} · T1 {_num(agent['target1'])} · {shares}")
        if not legacy and agent.get('entry_type'):          # agent-3 (v2.9 §11): three lines
            return _executable_lines(agent, ticker)
        if legacy:
            line = f"Agent（{agent['model']}）买入 · {band}；{agent['summary']}"
            if agent.get('first_risk'):
                line += f"；反方：{agent['first_risk']}"
        else:
            version = f"第 {agent['version_no']} 版" if agent.get('version_no') is not None else ''
            line = f"Agent {version}：{band} · {agent['summary']}" if version else f"Agent：{band} · {agent['summary']}"
            if agent.get('first_counter'):
                line += f" · 反证：{agent['first_counter']}"
        voided = '；'.join(f"作废 {r}" for r in agent.get('invalid_reasons') or [])
        if voided:
            line += f"；{voided}"
        if legacy:
            line += '；agent-1（旧版，不参与复查）'
        return line
    if agent['status'] == 'pass':
        return f"Agent（{agent['model']}）不建议买入；{agent['summary']}"
    return f"Agent 未给出计划（{REASON_TEXT.get(agent.get('reason'), agent.get('reason'))}）"


# --- weekly / monthly review pushes (P4 §6.2) ---

TIER_LABELS = {"excellent_plus": "非常优秀", "excellent": "优秀", "viable": "可行", "attention": "需关注"}


def _r(value, suffix="R"):
    return f"{value:+.2f}{suffix}" if value is not None else "—"


def _track_lines(tracks):
    if not tracks:
        return "该快照无轨道对比"
    lines = []
    for label, key in (("规则", "rule"), ("Agent", "agent"), ("实际", "actual")):
        s = tracks[key]
        lines.append(f"""{label} {s["units"]} 单元 · 已定 {s["determined"]} · 均值 {_r(s["mean"])}"""
                     f""" · 中位 {_r(s["median"])} · 合计 {_r(s["sum"])}""")
    actual = tracks["actual"]
    kinds = actual.get("by_plan_kind") or {}
    by_status = actual.get("by_status") or {}
    lines.append(f"""实际：未执行 {by_status.get("not_executed", 0)}（记 0R）· 未平仓 {by_status.get("open", 0)}"""
                 f""" · 照规则 {kinds.get("rule", 0)} · 照 Agent {kinds.get("agent", 0)} · 照自定 {kinds.get("own", 0)}""")
    return chr(10).join(lines)


def _discipline_line(discipline):
    if not discipline:
        return "—"
    rate = discipline.get("compliance_rate")
    rate_text = f"{int(round(rate * 100))}%" if rate is not None else "—"
    return (f"""追高 {discipline["chase"]} · 过早止盈 {discipline["early_take"]}"""
            f""" · 死扛亏损 {discipline["held_loser"]} · 计划外 {discipline["unplanned"]}"""
            f" · 合规率 {rate_text}")


LEARNING_CARD_LINES = 5          # v2.7 §10: at most five card lines, then 「…另有 n 张」
LEARNING_KNOWLEDGE_LINES = 3


def _learning_text(learning: dict) -> str:
    """The 「你学到了什么」 field text (v2.7 §10, verbatim lines): discipline, card counts, up to five cards, the
    tested knowledge; all three empty -> one line."""
    discipline, counts = learning.get('discipline') or {}, learning.get('counts') or {}
    cards, knowledge = learning.get('cards') or [], learning.get('knowledge') or []
    if not discipline.get('cards') and not counts.get('total') and not cards and not knowledge:
        return '本周没有结束的交易，也没有被检验的知识'
    lines = [f"纪律（本周结束的 {discipline.get('cards', 0)} 笔）：追高 {discipline.get('chase', 0)}"
             f" · 过早止盈 {discipline.get('early_take', 0)} · 死扛亏损 {discipline.get('held_loser', 0)}"
             f" · 计划外 {discipline.get('unplanned', 0)} · 待定 {discipline.get('pending', 0)}",
             f"复盘卡 {counts.get('total', 0)} 张（已审定 {counts.get('reviewed', 0)}"
             f" · 待审定 {counts.get('to_review', 0)}）"]
    for card in cards[:LEARNING_CARD_LINES]:
        if card.get('actual_r') is not None:
            actual = _r(card['actual_r'])
        else:                                     # bought but no R (unplanned: no stop to price the risk) vs not filled
            actual = '无 R（计划外）' if card.get('filled') else '未成交'
        lines.append(f"{card['ticker']} {card['end_text']} · 实际 {actual} · {card.get('execution_text') or card.get('decision_text')}")
    if len(cards) > LEARNING_CARD_LINES:
        lines.append(f'…另有 {len(cards) - LEARNING_CARD_LINES} 张')
    lines.append(f'被检验的知识 {len(knowledge)} 条')
    for item in knowledge[:LEARNING_KNOWLEDGE_LINES]:
        sample = item.get('sample') or {}
        mean = '少于 5 个不给均值' if sample.get('r_mean') is None else f"均值 {_r(sample['r_mean'])}"
        lines.append(f"{item['ref']} {item['title']} · 样本 {sample.get('count', 0)}（{mean}）")
    return chr(10).join(lines)


def _review_footer(payload):
    return NotifyField("说明", f"""{payload["note"]} {payload["evidence"]}{chr(10)}{payload["url"]}""")


def workbench_weekly(payload):
    overview = payload["overview"]
    if overview.get("execution"):                       # review-8 (v2.9): executions, not decisions
        overview_text = (f"""已执行 {overview["executed"]} · 未执行 {overview["not_executed"]}"""
                         f""" · 计划外成交 {overview["unplanned"]} · 已平仓 {overview["closed"]}""")
    else:
        overview_text = (f"""已决定 {overview["decided"]} · 未处理 {overview["undecided"]}"""
                         f""" · 计划外交易 {overview["unsignaled"]} · 已平仓 {overview["closed"]}""")
    fields = [NotifyField("概览", overview_text),
              NotifyField("轨道对比", _track_lines(payload.get("tracks")))]
    rules = payload.get("rules") or []
    rule_lines = []
    for r in rules:
        mean = r.get("r_mean")
        mean_text = _r(mean)                                       # 触发未成交：写 —，不写 NoneR
        pool_text = {"core": "核心", "screen": "筛选", "extended": "扩展"}.get(r["pool"], r["pool"])
        rule_id, triggers, plans, filled = r["rule_id"], r["triggers"], r["plans"], r["filled"]
        rule_lines.append(f"{pool_text} {rule_id} 触发 {triggers}"
                          f" · 计划 {plans} · 成交 {filled} · 均值 {mean_text}")
    rule_text = chr(10).join(rule_lines) or "本周无计划"
    _clip_field(fields, "规则", rule_text)
    _clip_field(fields, "纪律", _discipline_line(payload.get("discipline")))
    if payload.get("learning") is not None:       # review-6 及更早的快照没有这一段（v2.7 §10）
        _clip_field(fields, "你学到了什么", _learning_text(payload["learning"]))
    fields.append(_review_footer(payload))
    if payload.get("proposals") is not None:            # v2.15 §8.3 ④: the last line of the weekly review
        fields.append(NotifyField("提案", f"""本周提案 {payload["proposals"]} 条，到 roadmap 页查看"""))
    title = f"""📅 周度复盘 · {payload["start"]} – {payload["end"]}"""
    return [_fit_message(title, fields, _footer())]


def workbench_monthly(payload):
    ret = payload.get("return") or {}
    spy = payload.get("spy") or {}
    if ret.get("status") == "ok" and ret.get("return") is not None:
        tier = payload.get("tier") or "—"
        tier_label = TIER_LABELS.get(tier, tier)
        income = f"""本月 {ret["return"]:+.1%}（{tier_label}）"""
        if spy.get("return") is not None:
            income += f"""· SPY {spy["return"]:+.1%} · 超额 {spy.get("excess"):+.1%}"""
    else:
        income = "缺资金快照，无法计算"
    review = payload.get("review") or {}
    if review.get("flag") == "review" and review.get("compound") is not None \
            and review.get("spy_compound") is not None:
        flag_text = (f"""⚠️ 需复查：3 个月复合 {review["compound"]:+.1%}"""
                     f"""，SPY {review["spy_compound"]:+.1%}""")
    elif review.get("flag") == "review":                 # monthly-1 snapshots carry the flag only
        flag_text = "⚠️ 需复查：连续 3 个月复合亏损且跑输 SPY"
    elif review.get("flag") == "ok":
        flag_text = "正常"
    else:
        flag_text = "样本不足"
    sensitivity = payload.get("sensitivity") or {}
    lines = [f"""{row["risk_pct"]:.1%} 收益 {row["return"]:+.1%} · 回撤 {row["max_drawdown"]:.1%}"""
             for row in sensitivity.get("rows") or []]
    if sensitivity.get("count") is not None:
        trade_count = sensitivity["count"]
        lines.append(f"共 {trade_count} 笔（近似，按平仓时间排序）")
    fields = [NotifyField("收益", income),
              NotifyField("流程复查", flag_text),
              NotifyField("敏感性（近似）", chr(10).join(lines) or "—"),
              NotifyField("轨道对比", _track_lines(payload.get("tracks"))),
              NotifyField("纪律", _discipline_line(payload.get("discipline"))),
              _review_footer(payload)]
    month = payload["month"]
    return [_fit_message(f"🗓 月度复盘 · {month}", fields, _footer())]


def workbench_review_reminder(payload):
    fields = [NotifyField("待录入",
                          f"""缺 {payload["month"]} 月末资金快照。录入后在「复盘 → 月度复盘」生成并推送。"""
                          + chr(10) + payload["url"])]
    title = f"""⏰ 月度复盘待录入 · {payload["month"]}"""
    return [_fit_message(title, fields, _footer())]
