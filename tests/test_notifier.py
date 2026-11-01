from datetime import date

from investment_assistant.config import NotifyConfig
from investment_assistant.notify import notifier, templates


class FakeClient:
    def __init__(self):
        self.sent = []

    def send(self, channel, payload):
        self.sent.append((channel, payload))


def test_dispatch_skips_when_globally_disabled():
    out = notifier.dispatch("workbench", "success", {}, NotifyConfig(discord_enabled=False), client=FakeClient())
    assert out["sent"] is False and out["reason"] == "discord_disabled"


def test_dispatch_skips_when_task_disabled():
    cfg = NotifyConfig(task_enabled={"workbench": False})
    out = notifier.dispatch("workbench", "success", {}, cfg, client=FakeClient())
    assert out["sent"] is False and out["reason"] == "task_disabled"


def test_dispatch_routes_to_channel():
    client = FakeClient()
    out = notifier.dispatch("filings", "success", {"filings": []}, NotifyConfig(), client=client)
    assert out["sent"] is True and out["channel"] == "discord" and out["discord_channel"] == "earnings"
    assert client.sent and "embeds" in client.sent[0][1]


def test_dispatch_send_failure_is_structured():
    class Boom(FakeClient):
        def send(self, channel, payload):
            raise RuntimeError("network")

    out = notifier.dispatch("filings", "success", {"filings": []}, NotifyConfig(), client=Boom())
    assert out["sent"] is False and "send_failed" in out["reason"]


def test_date_label_single_range_and_default():
    assert templates.date_label_for(date(2026, 7, 3), date(2026, 7, 3)) == "2026-07-03"
    assert templates.date_label_for(date(2026, 6, 1), date(2026, 6, 30)) == "2026-06-01 ~ 2026-06-30"
    assert templates.date_label_for(None, None) == date.today().isoformat()


def test_filings_digest_uses_actual_date_label():
    n = templates.filings_digest(
        {"downloaded_count": 0, "filings": []}, date_label="2026-06-01 ~ 2026-06-30")
    assert n.title == "📄 财报 · 2026-06-01 ~ 2026-06-30"
    assert n.fields[0].name == "新提交 (0)"
    assert n.fields[0].value == "该日期无新财报"





def test_dispatch_passes_date_label_into_embed():
    client = FakeClient()
    notifier.dispatch(
        "filings", "success", {"downloaded_count": 1, "filings": []}, NotifyConfig(),
        client=client, target_start_date=date(2026, 7, 3), target_end_date=date(2026, 7, 3),
    )
    assert client.sent[0][1]["embeds"][0]["title"] == "📄 财报 · 2026-07-03"


class FakeEmailClient:
    def __init__(self):
        self.sent = []

    def send(self, subject, html_body):
        self.sent.append((subject, html_body))


def test_dispatch_email_channel_sends_with_subject():
    client = FakeEmailClient()
    cfg = NotifyConfig(channel="email", email={"from_addr": "a@gmail.com", "to_addr": "b@outlook.com"})
    out = notifier.dispatch("filings", "success", {"downloaded_count": 0, "filings": []}, cfg,
                            client=client, target_start_date=date(2026, 7, 3), target_end_date=date(2026, 7, 3))
    assert out == {"sent": True, "channel": "email"}
    assert client.sent and client.sent[0][0] == "[investment-assistant] 📄 财报 · 2026-07-03"


def test_dispatch_email_requires_password(monkeypatch):
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    cfg = NotifyConfig(channel="email", email={"from_addr": "a@gmail.com", "to_addr": "b@outlook.com"})
    out = notifier.dispatch("workbench", "success", {}, cfg)
    assert out == {"sent": False, "reason": "email_no_password"}


def test_dispatch_email_requires_recipient(monkeypatch):
    monkeypatch.setenv("SMTP_PASSWORD", "x")
    cfg = NotifyConfig(channel="email", email={"from_addr": "a@gmail.com", "to_addr": ""})
    out = notifier.dispatch("workbench", "success", {}, cfg)
    assert out == {"sent": False, "reason": "email_no_recipient"}


def test_dispatch_email_requires_sender(monkeypatch):
    monkeypatch.setenv("SMTP_PASSWORD", "x")
    cfg = NotifyConfig(channel="email", email={"from_addr": "", "to_addr": "b@outlook.com"})
    out = notifier.dispatch("workbench", "success", {}, cfg)
    assert out == {"sent": False, "reason": "email_no_sender"}


def test_dispatch_email_send_failure_is_structured():
    class Boom(FakeEmailClient):
        def send(self, subject, html_body):
            raise RuntimeError("smtp down")

    cfg = NotifyConfig(channel="email", email={"from_addr": "a@gmail.com", "to_addr": "b@outlook.com"})
    out = notifier.dispatch("metrics", "success", {}, cfg, client=Boom())
    assert out["sent"] is False and "send_failed" in out["reason"]


def test_dispatch_task_disabled_wins_over_channel():
    cfg = NotifyConfig(channel="email", task_enabled={"metrics": False})
    out = notifier.dispatch("metrics", "success", {}, cfg, client=FakeEmailClient())
    assert out == {"sent": False, "reason": "task_disabled"}


def test_send_notifications_disabled_task():
    from investment_assistant.notify.message import Notification

    out = notifier.send_notifications('workbench', [Notification(title='x', color=1, fields=[])],
                                      NotifyConfig(task_enabled={'workbench': False}), client=FakeClient())
    assert out['sent'] is False and out['reason'] == 'task_disabled' and out['messages'] == 0


def test_send_notifications_sends_each_message():
    from investment_assistant.notify.message import Notification

    client = FakeClient()
    notes = [Notification(title='a', color=1, fields=[]), Notification(title='b', color=2, fields=[])]
    out = notifier.send_notifications('workbench', notes, NotifyConfig(), client=client)
    assert out['sent'] is True and out['messages'] == 2 and len(client.sent) == 2


def test_send_notifications_partial_failure_counts_sent():
    from investment_assistant.notify.message import Notification

    class HalfBoom(FakeClient):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def send(self, channel, payload):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError('network')

    out = notifier.send_notifications('workbench', [Notification(title='a', color=1, fields=[]),
                                                    Notification(title='b', color=2, fields=[])],
                                      NotifyConfig(), client=HalfBoom())
    assert out['sent'] is False and out['messages'] == 1 and 'send_failed' in out['reason']


def test_send_notifications_failure_is_structured():
    from investment_assistant.notify.message import Notification

    class Boom(FakeClient):
        def send(self, channel, payload):
            raise RuntimeError('network')

    out = notifier.send_notifications('workbench', [Notification(title='x', color=1, fields=[])],
                                      NotifyConfig(), client=Boom())
    assert out['sent'] is False and out['messages'] == 0 and 'send_failed' in out['reason']


def test_agent_error_source_uses_display_name():
    from investment_assistant.notify.templates import SOURCE_NAMES, workbench_digest
    assert SOURCE_NAMES['agent'] == 'Agent'
    payload = {'session': '2026-10-22', 'overview': {}, 'plans': [], 'holdings': [], 'pending': {},
               'errors': [], 'errors_by_source': {'agent': ['NVDA', 'MU']}}
    fields = [f for m in workbench_digest(payload) for f in m.fields]
    agent_field = next(f for f in fields if f.name == 'Agent')
    assert 'NVDA' in agent_field.value and 'MU' in agent_field.value


def test_unknown_channel_is_structured_failure():
    from dataclasses import replace

    from investment_assistant.config import NotifyConfig
    from investment_assistant.notify import notifier
    from investment_assistant.notify.discord import DiscordChannel
    from investment_assistant.notify.message import Notification

    sent = []

    class Fake:
        def send(self, channel, payload):
            sent.append(channel)

    cfg = replace(NotifyConfig(), task_channels={"workbench": "daily"})
    n = Notification(title="t", color=1, fields=[], footer="")
    out = notifier.send_notifications("workbench", [n], cfg, client=Fake())
    assert out == {"sent": False, "messages": 0, "reason": "unknown_channel: daily"}
    assert sent == []


# --- v2.5 §10.3: the plan-updates message ---

def _digest_payload(updates):
    return {'session': '2026-10-22', 'overview': {'plan_updates': {'revise': 0, 'invalidate': len(updates)}},
            'plans': [], 'holdings': [], 'pending': {}, 'errors': [], 'errors_by_source': {},
            'plan_updates': updates}


def _invalidate(ticker, reason='失效条件成立：收盘跌破 112'):
    return {'ticker': ticker, 'kind': 'invalidate', 'from_version': 0, 'to_version': None, 'changes': [],
            'levels': None, 'valid_until': None, 'decided_by': 'host', 'reason': reason}


def test_plan_updates_message_is_third():
    from investment_assistant.notify.templates import workbench_digest
    messages = workbench_digest(_digest_payload([_invalidate('NVDA')]))
    assert len(messages) == 6
    assert messages[2].title == '🔁 计划更新 · 2026-10-22'
    assert [m.title.split(' · ')[0] for m in messages] == ['📊 工作台', '🆕 新计划', '🔁 计划更新', '💼 持仓',
                                                          '⏳ 待处理与异常', '🔎 筛选报告']
    assert messages[2].fields[0].value == 'NVDA 作废（系统规则）：失效条件成立：收盘跌破 112'


def test_no_plan_updates_no_message():
    from investment_assistant.notify.templates import workbench_digest
    messages = workbench_digest(_digest_payload([]))
    assert len(messages) == 5
    assert not any(m.title.startswith('🔁 计划更新') for m in messages)
    no_key = {k: v for k, v in _digest_payload([]).items() if k != 'plan_updates'}
    assert len(workbench_digest(no_key)) == 5


def test_plan_updates_respect_field_and_message_limits():
    from investment_assistant.notify.templates import workbench_digest
    messages = workbench_digest(_digest_payload([_invalidate(f'T{i:02d}', '长' * 3000) for i in range(12)]))
    updates = messages[2]
    assert all(len(f.value) <= 1024 for f in updates.fields)
    total = len(updates.title) + sum(len(f.name) + len(f.value) for f in updates.fields) + len(updates.footer)
    assert total <= 6000
    assert updates.fields[-1].name == '溢出'


# --- v2.7 §10: the weekly learning field goes out inside the 1024-char Discord limit ---

def _weekly_with_learning(cards):
    from investment_assistant.review import period_digest as review_digest

    learning = {'discipline': {'cards': len(cards), 'chase': 0, 'early_take': 0, 'held_loser': 0, 'unplanned': 0,
                               'pending': 0},
                'cards': cards, 'counts': {'total': len(cards), 'reviewed': 0, 'to_review': len(cards),
                                           'drafting': 0, 'draft_failed': 0},
                'knowledge': []}
    review = {'id': 1, 'week_start': date(2026, 10, 19), 'calc_version': 'review-7',
              'stats': {'units': {'decided': 0, 'undecided': 0}, 'unsignaled_trades': [], 'closed_trades': [],
                        'tracks': None, 'by_pool': {}, 'discipline': None, 'learning': learning}}
    return review_digest.weekly(review, public_url='http://localhost:8787')


def test_weekly_learning_truncated():
    cards = [{'card_id': n, 'ticker': 'NVDA', 'end_kind': 'stop', 'end_text': '止损', 'actual_r': -1.0,
              'decision_text': '很长的决定文案' * 40, 'rule_r': None, 'agent_r': None, 'flags': [],
              'status': 'to_review', 'headline': None} for n in range(5)]
    messages = templates.workbench_weekly(_weekly_with_learning(cards))
    sent = FakeClient()
    out = notifier.send_notifications('workbench', messages, NotifyConfig(), client=sent)
    assert out['sent'] is True and len(sent.sent) == 1
    fields = messages[0].fields
    assert max(len(f.value) for f in fields) <= 1024                       # Discord's per-field limit
    learning = next(f for f in fields if f.name == '你学到了什么')
    assert len(learning.value) == 1024 and learning.value.endswith('…')
    assert learning.value.startswith('纪律（本周结束的 5 笔）')


def _learning_card(n, **over):
    card = {'card_id': n, 'ticker': 'NVDA', 'end_kind': 'stop', 'end_text': '止损', 'actual_r': -1.0,
            'decision_text': '采纳（照规则计划）', 'rule_r': None, 'agent_r': None, 'flags': [],
            'status': 'to_review', 'headline': None}
    card.update(over)
    return card


def _sent_text(payload):
    sent = FakeClient()
    out = notifier.send_notifications('workbench', templates.workbench_weekly(payload), NotifyConfig(), client=sent)
    assert out['sent'] is True
    return templates.workbench_weekly(payload)[0]


def test_weekly_learning_field_lines():
    payload = _weekly_with_learning([_learning_card(1), _learning_card(2, ticker='AMD', actual_r=None,
                                                                     end_text='过期未成交', decision_text='降为观察')])
    payload['learning']['knowledge'] = [{'ref': 'know:7@2', 'title': '回踩不追', 'new_outcomes': 1,
                                         'sample': {'count': 6, 'determined': 5, 'expired': 1, 'r_mean': 0.4,
                                                    'r_median': 0.3}}]
    message = _sent_text(payload)
    names = [f.name for f in message.fields]
    assert names.index('纪律') + 1 == names.index('你学到了什么') == names.index('说明') - 1
    learning = next(f for f in message.fields if f.name == '你学到了什么')
    assert learning.value.split('\n') == [
        '纪律（本周结束的 2 笔）：追高 0 · 过早止盈 0 · 死扛亏损 0 · 计划外 0 · 待定 0',
        '复盘卡 2 张（已审定 0 · 待审定 2）',
        'NVDA 止损 · 实际 -1.00R · 采纳（照规则计划）',
        'AMD 过期未成交 · 实际 未成交 · 降为观察',
        '被检验的知识 1 条',
        'know:7@2 回踩不追 · 样本 6（均值 +0.40R）']


def test_weekly_learning_all_empty_one_line():
    message = _sent_text(_weekly_with_learning([]))
    learning = next(f for f in message.fields if f.name == '你学到了什么')
    assert learning.value == '本周没有结束的交易，也没有被检验的知识'


def test_old_snapshot_has_no_learning_field():
    from investment_assistant.review import period_digest as review_digest

    review = {'id': 1, 'week_start': date(2026, 10, 12), 'calc_version': 'review-6',
              'stats': {'units': {'decided': 0, 'undecided': 0}, 'unsignaled_trades': [], 'closed_trades': [],
                        'tracks': None, 'by_pool': {}, 'discipline': None}}
    payload = review_digest.weekly(review, public_url='http://localhost:8787')
    assert payload['learning'] is None
    message = _sent_text(payload)
    assert '你学到了什么' not in [f.name for f in message.fields]


def test_daily_failed_template():
    """v2.8 §13.2, verbatim: title, red, the four fields, and the link only with ``workbench.public_url``."""
    from investment_assistant.notify import templates

    payload = {'session': '2026-10-29', 'node_title': '数据采集', 'reason': '运行中断：provider down',
               'skipped': 8, 'deadline_text': '2026-10-31 05:00（北京时间）',
               'run_id': 'daily-20261030033000-1a2b3c4d', 'public_url': ''}
    [plain] = templates.daily_failed(payload)
    assert plain.title == '⚠️ 每日工作流失败 · 2026-10-29' and plain.color == templates.DAILY_FAILED_COLOR == 15548997
    assert [(f.name, f.value) for f in plain.fields] == [
        ('失败节点', '数据采集（已自动重试 1 次）'),
        ('原因', '运行中断：provider down'),
        ('未执行', '其后 8 个节点'),
        ('续跑', '2026-10-31 05:00（北京时间） 之前可在运行详情「从此节点继续」')]
    [linked] = templates.daily_failed({**payload, 'public_url': 'https://wb.example.com/'})
    assert [f.name for f in linked.fields] == ['失败节点', '原因', '未执行', '续跑', '运行详情']
    assert linked.fields[-1].value == 'https://wb.example.com/#tools/runs/daily-20261030033000-1a2b3c4d'
    [long] = templates.daily_failed({**payload, 'reason': 'x' * 500})
    assert len(long.fields[1].value) == 300                                    # ≤ 300 characters
    [stage_failed] = templates.daily_failed({**payload, 'skipped': 0, 'deadline_text': ''})
    assert [f.name for f in stage_failed.fields] == ['失败节点', '原因']        # 未执行 only for n > 0, 续跑 only if resumable
    [never_ran] = templates.daily_failed({'session': None, 'node_title': '每日工作流', 'retried': False,
                                          'reason': 'migration_028_missing', 'run_id': 'daily-x', 'public_url': ''})
    assert [(f.name, f.value) for f in never_ran.fields] == [('失败节点', '每日工作流'),
                                                            ('原因', 'migration_028_missing')]


def test_agent_not_connected_template():
    """v2.8 §13.2, verbatim: the title, red, the two fields; nothing else (no link, no session)."""
    from investment_assistant.notify import templates

    [notice] = templates.agent_not_connected({'run_id': 'daily-20261030033000-1a2b3c4d'})
    assert notice.title == '⚠️ ChatGPT 连接已失效' and notice.color == templates.DAILY_FAILED_COLOR == 15548997
    assert [(f.name, f.value) for f in notice.fields] == [
        ('影响', '今天的 Agent 建议与复查已跳过'),
        ('处理', '到「设置 → Agent 连接」重新连接')]
    assert [(f.name, f.value) for f in templates.agent_not_connected(None)[0].fields] == [
        (f.name, f.value) for f in notice.fields]


def test_daily_failed_goes_through_the_workbench_category(monkeypatch):
    """Sent as ``send_notifications('workbench', …)``: the existing category key, channel and switch (D15)."""
    from investment_assistant.notify import notifier, templates

    class Cfg:
        channel = 'discord'
        discord_enabled = True
        task_enabled = {'workbench': True}
        task_channels = {'workbench': 'signals'}

    sent = []

    class Client:
        def send(self, channel, message):
            sent.append((channel.value, message))

    out = notifier.send_notifications('workbench', templates.daily_failed(
        {'session': '2026-10-29', 'node_title': '筛选 `screen-1`', 'reason': 'boom', 'skipped': 7,
         'deadline_text': 'x', 'run_id': 'daily-x', 'public_url': ''}), Cfg(), client=Client())
    assert out['sent'] is True and out['messages'] == 1 and sent[0][0] == 'signals'
    Cfg.task_enabled = {'workbench': False}
    assert notifier.send_notifications('workbench', [], Cfg(), client=Client())['reason'] == 'task_disabled'
