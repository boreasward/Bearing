"""Notification adapter explicitly handed to workflow nodes by edge entry points."""
from investment_assistant.notify import notifier, templates


def send(kind, payload, notify_cfg, *, client=None):
    render = {'digest': templates.workbench_digest, 'closed': templates.workbench_closed,
              'weekly': templates.workbench_weekly, 'monthly': templates.workbench_monthly,
              'reminder': templates.workbench_review_reminder}[kind]
    task = 'workbench' if kind in ('digest', 'closed') else 'workbench_review'
    return notifier.send_notifications(task, render(payload), notify_cfg, client=client)
