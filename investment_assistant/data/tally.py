"""Per-target fetch accounting; full final-source totals precede display truncation."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from investment_assistant.data import fetch_policy
from investment_assistant.data.fetch_policy import FetchError, Fetched
from investment_assistant.data.fetch_status import classify, iso, sanitize_reason


TARGETS_MAX = 600       # detail.targets items kept per family; the excess is counted in detail.targets_truncated


class Tally:
    """Per-target outcome accounting for one family's fetch; ``row`` freezes it into
    a data_fetch_runs row. The last write per target wins; failure reasons are
    sanitised on the way in (redacted, truncated).

    ``'*'`` is the whole-call failure sentinel, never a target: it does not enter
    ``targets`` or the denominator. The denominator is the real target set —
    frozen upfront with ``set_targets`` (what the call was about to collect) or
    built per target by ``ok``/``fail``. A call failure with zero targets is
    ``failed``, never a 0==0 ``success``."""

    def __init__(self, family: str, job: str, targets: Iterable[str] = ()):
        self.family = family
        self.job = job
        self.targets: list[str] = []
        self.outcomes: dict[str, str | None] = {}   # target -> failure reason; None means ok
        self._records: dict[str, dict[str, Any]] = {}   # target -> detail.targets item (fetch-1, spec §6.4)
        self.detail: dict[str, Any] = {}
        self._skipped: str | None = None
        self._call_failure: str | None = None
        self.set_targets(targets)

    def set_targets(self, targets: Iterable[str]) -> None:
        """Freeze the call's real target set (the denominator) before collecting."""
        for target in targets:
            self.add_target(target)

    def add_target(self, target: str) -> None:
        if target != '*' and target not in self.targets:
            self.targets.append(target)

    def record(self, target: str, outcome: Fetched | BaseException, *, rows: int | None = None,
               latest: Any = None, source_used: str | None = None, fallback: dict | None = None) -> None:
        """Book one target's outcome: a ``Fetched`` is a success, a ``FetchError`` (or any exception) a failure.

        Feeds ``outcomes`` (the family status, ``failures``) and ``detail.targets`` (spec §6.4):
        ``{target, ok, attempts, error_kind, http_status, reason, rows, latest, elapsed_ms}``. ``rows`` is what the
        fetch newly wrote (bars / news / observations) or found (SEC filings); ``latest`` the newest point in time
        it saw (date or datetime, stored as ISO text). The last record per target wins. ``'*'`` (the whole-call
        failure) books the failure but is not a target. Optional source_used is the final source id, not a
        fallback attempt; fallback records {tried, reason} with the reason sanitised."""
        if isinstance(outcome, Fetched):
            known = bool(outcome.attempts)          # a legacy ok() carries no fetch data: attempts / elapsed null
            item = {'ok': True, 'attempts': len(outcome.attempts) or None, 'error_kind': None,
                    'http_status': (outcome.attempts[-1].get('status') if known else None),
                    'reason': None, 'rows': rows, 'latest': iso(latest),
                    'elapsed_ms': outcome.elapsed_ms if known else None}
            reason = None
        else:
            reason = sanitize_reason(outcome)
            if isinstance(outcome, FetchError):
                kind, status = outcome.kind, outcome.status
                attempts, elapsed = len(outcome.attempts) or None, outcome.elapsed_ms if outcome.attempts else None
            else:
                kind, status = fetch_policy.classify_exception(outcome)
                attempts = elapsed = None
            item = {'ok': False, 'attempts': attempts, 'error_kind': kind, 'http_status': status,
                    'reason': reason, 'rows': rows, 'latest': iso(latest), 'elapsed_ms': elapsed}
        if target == '*':
            self._call_failure = reason
            return
        fallback = fallback if fallback is not None else {'tried': False, 'reason': None}
        item.update(source_used=source_used, fallback={
            'tried': bool(fallback['tried']),
            'reason': sanitize_reason(fallback['reason']) if fallback.get('reason') else None})
        self.add_target(target)
        self.outcomes[target] = reason
        self._records[target] = {'target': target, **item}

    def record_for(self, target: str) -> dict[str, Any]:
        """The ``detail.targets`` item booked for ``target`` (``{}`` when none)."""
        return dict(self._records.get(target, {}))

    @property
    def call_failure(self) -> str | None:
        """The sanitised reason of the whole-call (``'*'``) failure, if one was booked."""
        return self._call_failure

    def ok(self, target: str) -> None:
        self.record(target, Fetched(None))

    def fail(self, target: str, reason: Any) -> None:
        """Legacy failure booking: no attempt data unless ``reason`` is itself a ``FetchError``."""
        self.record(target, reason if isinstance(reason, BaseException) else RuntimeError(str(reason)))

    def skip(self, reason: str) -> None:
        self._skipped = sanitize_reason(reason)
        self.detail['reason'] = self._skipped

    def row(self, *, trigger: str, run_id: str | None, session: date, started_at: datetime,
            finished_at: datetime, scope: str = 'family', ticker: str | None = None) -> dict[str, Any]:
        ok_count = sum(1 for target in self.targets
                       if target in self.outcomes and self.outcomes[target] is None)
        failures = sorted(({'target': target, 'reason': reason}
                           for target, reason in self.outcomes.items() if reason is not None),
                          key=lambda failure: failure['target'])
        detail = dict(self.detail)
        # Source totals must use final successful outcomes before the display detail is clipped.
        source_counts: dict[str, int] = {}
        for target in self.targets:
            if target in self.outcomes and self.outcomes[target] is None:
                source = self._records.get(target, {}).get('source_used')
                if isinstance(source, str) and source.strip():
                    source_counts[source] = source_counts.get(source, 0) + 1
        detail['source_counts'] = source_counts
        detail['source_counts_complete'] = sum(source_counts.values()) == ok_count
        if self._records:
            targets = [self._records[name] for name in sorted(self._records)]
            detail['policy'] = fetch_policy.FETCH_POLICY_VERSION
            if len(targets) > TARGETS_MAX:
                detail['targets_truncated'] = len(targets) - TARGETS_MAX
                targets = targets[:TARGETS_MAX]
            detail['targets'] = targets
        if self._call_failure is not None:
            failures.append({'target': '*', 'reason': self._call_failure})
            # I05: a family-scoped '*' failure applies to this call's own targets only.
            detail['failed_targets'] = sorted(self.targets)
        status = ('skipped' if self._skipped else
                  'failed' if self._call_failure is not None and ok_count == 0 else
                  classify(len(self.targets), ok_count))
        return {'family': self.family, 'job': self.job, 'trigger': trigger, 'run_id': run_id,
                'scope': scope, 'ticker': ticker, 'session': session,
                'status': status,
                'target_count': len(self.targets), 'ok_count': ok_count,
                'failures': failures, 'detail': detail,
                'started_at': started_at, 'finished_at': finished_at}
