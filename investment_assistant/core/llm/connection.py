"""The ChatGPT connection of this machine: login, callback, refresh, disconnect, model choice, status (v2.8 spec §8, §10.2).

This is the only module that touches ``agent_connections`` / ``agent_connection_events`` (the repository never
names them), so "tokens stay out of logs and responses" is a property of one file. The single row is a mutable state
table; the event table is append-only and holds codes, expiry times and model slugs only, never a token.

A login in progress lives in process memory (D30): ``state``, ``nonce``, ``code_verifier`` and the loopback listener
vanish with the process, and a row left ``pending`` after a restart is restored to its previous status on the next
read. Refresh happens inside ``SELECT … FOR UPDATE`` and the new tokens are committed before they are handed out (D32).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterator

from investment_assistant.core.llm import chatgpt
from investment_assistant.core.llm.chatgpt import REAUTH_CODES, ChatGPTError, error_for
from investment_assistant.core import tx as core_tx

log = logging.getLogger(__name__)

REFRESH_THRESHOLD_S = 300                       # refresh when less than this is left (spec §8.2)
REFRESH_TTL = timedelta(days=30)                # the refresh token rolls forward by 30 days on every refresh
FRESH_TOKEN_S = 600                             # a token this young that is still rejected means the connection is bad

_COLUMNS = ('status', 'host_id', 'client_id', 'subject', 'email', 'scopes', 'access_token', 'access_expires_at',
            'refresh_token', 'refresh_expires_at', 'earliest_refresh_at', 'model', 'model_display_name',
            'connected_at', 'last_refresh_at', 'last_error')
_PRIOR = ('disconnected', 'needs_login')

_TOKEN_LOCK = threading.RLock()                 # refresh / login completion / disconnect, one at a time per process
_PENDING_LOCK = threading.Lock()                # guards ``_PENDING`` only (never held across I/O)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _missing_migration() -> ChatGPTError:
    return ChatGPTError('migration_028_missing', '迁移 028 未执行。')


# ===================================================================================================================
# database plumbing
# ===================================================================================================================

def _connect():
    return core_tx.connection()


@contextmanager
def _agent_connection_tx() -> Iterator[Any]:
    import psycopg

    with _connect() as conn:
        try:
            yield conn
            conn.commit()
        except psycopg.errors.UndefinedTable:
            conn.rollback()
            raise _missing_migration() from None
        except BaseException:
            conn.rollback()
            raise


def _load(conn, *, lock: bool = False) -> dict | None:
    row = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM agent_connections WHERE id = 1"
                       + (' FOR UPDATE' if lock else '')).fetchone()
    if not row:
        return None
    found = dict(zip(_COLUMNS, row))
    for token in (found['access_token'], found['refresh_token']):
        chatgpt.remember_secret(token)          # after a restart too: error texts must be scrubbed of stored tokens
    return found


def _ensure_row(conn) -> None:
    import uuid

    conn.execute("INSERT INTO agent_connections (id, host_id) VALUES (1, %s) ON CONFLICT (id) DO NOTHING",
                 (f'urn:uuid:{uuid.uuid4()}',))


def _event(conn, kind: str, now: datetime, code: str | None = None, **detail: Any) -> None:
    conn.execute('INSERT INTO agent_connection_events (kind, code, detail, created_at) VALUES (%s, %s, %s::jsonb, %s)',
                 (kind, chatgpt.safe_code(code), json.dumps({'protocol': chatgpt.CHATGPT_PROTOCOL_VERSION, **detail}), now))


def _iso(value: datetime | None) -> str | None:
    """UTC, whatever time zone the database session reports."""
    return value.astimezone(UTC).isoformat() if value else None


def _scopes_ok(row: dict) -> bool:
    return chatgpt.SHARING_SCOPE in (row.get('scopes') or [])


def _message_for(code: str) -> str:
    """The fixed Chinese sentence stored in ``last_error`` for a failure code."""
    return chatgpt.MESSAGES.get(code) or chatgpt.api_error({'error': code}).message


# ===================================================================================================================
# login in progress (process memory)
# ===================================================================================================================

@dataclass
class _Pending:
    verifier: str
    nonce: str
    redirect_uri: str
    server: chatgpt.CallbackServer
    prior: str                                   # the status to go back to: disconnected / needs_login / connected
    expires_at: datetime
    ttl: float
    started: datetime
    mono0: float
    completing: bool = False

    def clock(self) -> datetime:
        """'Now' on the caller's clock: the time ``start_login`` was given plus the real time elapsed since."""
        return self.started + timedelta(seconds=time.monotonic() - self.mono0)


_PENDING: _Pending | None = None


def _callback_port() -> int:
    raw = (os.environ.get('WORKBENCH_CHATGPT_CALLBACK_PORT') or '').strip()
    if not raw:
        return 0
    try:
        port = int(raw)
    except ValueError:
        port = -1
    if not 0 <= port <= 65535:
        raise error_for('callback_port_unavailable')
    return port


def _prior_status(conn) -> str:
    row = conn.execute("SELECT detail->>'prior' FROM agent_connection_events WHERE kind = 'login_started' "
                       'ORDER BY id DESC LIMIT 1').fetchone()
    return row[0] if row and row[0] in _PRIOR else 'disconnected'


def _restore(conn, prior: str, code: str, now: datetime, *, note: bool = True) -> None:
    """End a login that did not complete: a ``pending`` row goes back to ``prior`` (a connected row is untouched)."""
    row = _load(conn, lock=True)
    if row is None:
        return
    if row['status'] == 'pending':
        conn.execute('UPDATE agent_connections SET status = %s, last_error = %s, updated_at = %s WHERE id = 1',
                     (prior if prior in _PRIOR else 'disconnected',
                      _message_for(code) if note and code != 'cancelled' else None, now))
    _event(conn, 'login_failed', now, code)


def _heal(conn, now: datetime) -> dict | None:
    """The row, with a ``pending`` status that no live login backs (the process restarted) restored."""
    row = _load(conn)
    if row is not None and row['status'] == 'pending':
        with _PENDING_LOCK:
            live = _PENDING is not None
        if not live:
            _restore(conn, _prior_status(conn), 'login_expired', now)
            row = _load(conn)
    return row


def _expire_pending(now: datetime) -> None:
    global _PENDING
    with _PENDING_LOCK:
        pending = _PENDING
        if pending is None or pending.completing or now < pending.expires_at:
            return
        _PENDING = None
    pending.server.close()
    with _agent_connection_tx() as conn:
        _restore(conn, pending.prior, 'login_expired', now)


# ===================================================================================================================
# login
# ===================================================================================================================

def start_login(*, now: datetime, open_port: int | None = None) -> dict:
    """Open a one-shot loopback listener and return ``{'authorize_url', 'expires_at'}`` for the browser.

    ``open_port`` overrides ``WORKBENCH_CHATGPT_CALLBACK_PORT`` (default 0: the system picks). A login already in
    progress is cancelled first. A connected row stays connected while the new login runs (``pending`` only shows in
    ``status_view``); a disconnected / needs_login row becomes ``pending``."""
    global _PENDING
    discovery = chatgpt.discover()                       # a failure here leaves nothing behind
    cancel_login()
    pkce = chatgpt.new_pkce()
    port = open_port if open_port is not None else _callback_port()
    ttl = float(chatgpt.LOGIN_TTL_S)
    server: chatgpt.CallbackServer | None = None
    pending: _Pending | None = None
    try:
        with _TOKEN_LOCK:
            with _agent_connection_tx() as conn:
                _ensure_row(conn)
                row = _load(conn, lock=True)
                server = chatgpt.CallbackServer(pkce['state'], row['client_id'])
                server.start(port)
                url = chatgpt.authorize_url(
                    discovery=discovery, client_id=row['client_id'], redirect_uri=server.redirect_uri,
                    state=pkce['state'], nonce=pkce['nonce'], challenge=pkce['challenge'], host_id=row['host_id'],
                    email_hint=row['email'], first=row['client_id'] is None)
                prior = row['status'] if row['status'] in (*_PRIOR, 'connected') else 'disconnected'
                if row['status'] != 'connected':
                    conn.execute("UPDATE agent_connections SET status = 'pending', last_error = NULL, updated_at = %s "
                                 'WHERE id = 1', (now,))
                _event(conn, 'login_started', now, None, prior=prior)
                # registered before the commit, so a concurrent read never mistakes the new 'pending' row for debris
                pending = _Pending(verifier=pkce['verifier'], nonce=pkce['nonce'], redirect_uri=server.redirect_uri,
                                   server=server, prior=prior, expires_at=now + timedelta(seconds=ttl), ttl=ttl,
                                   started=now, mono0=time.monotonic())
                with _PENDING_LOCK:
                    _PENDING = pending
    except BaseException:
        with _PENDING_LOCK:
            if pending is not None and _PENDING is pending:
                _PENDING = None
        if server is not None:
            server.close()                   # also when the commit itself failed
        raise
    threading.Thread(target=_watch, args=(pending,), name='chatgpt-login', daemon=True).start()
    return {'authorize_url': url, 'expires_at': pending.expires_at.isoformat()}


def _watch(pending: _Pending) -> None:
    result = pending.server.wait(pending.ttl)
    try:
        _complete(pending, result, pending.clock())
    except Exception as exc:            # noqa: BLE001 - a background thread must not die noisily; type only, no text
        log.warning('chatgpt login completion failed: %s', type(exc).__name__)


def cancel_login() -> dict:
    """Close the listener and put the status back. ``{'cancelled': True}`` when a login was in progress."""
    global _PENDING
    now = _utcnow()
    with _PENDING_LOCK:
        pending = _PENDING
        if pending is not None and pending.completing:
            return {'cancelled': False}              # the token exchange is already running; let it finish
        _PENDING = None
    if pending is not None:
        pending.server.close()
    with _agent_connection_tx() as conn:
        row = _load(conn)
        if pending is not None or (row is not None and row['status'] == 'pending'):
            _restore(conn, pending.prior if pending else _prior_status(conn), 'cancelled', now)
    return {'cancelled': pending is not None}


def complete_login(callback: dict, *, now: datetime) -> dict:
    """Finish the login in progress with the listener's result (``{'code', 'client_id'}`` or ``{'error'}``). Called by
    the listener's watcher thread; returns ``{'ok': bool, 'code': <safe code> | None}``."""
    with _PENDING_LOCK:
        pending = _PENDING
    if pending is None:
        return {'ok': False, 'code': 'no_pending_login'}
    return _complete(pending, callback, now)


def _complete(pending: _Pending, callback: dict, now: datetime) -> dict:
    global _PENDING
    with _PENDING_LOCK:
        if _PENDING is not pending or pending.completing:
            return {'ok': False, 'code': 'no_pending_login'}
        pending.completing = True
    try:
        return _finish_login(pending, callback, now)
    finally:
        with _PENDING_LOCK:
            if _PENDING is pending:
                _PENDING = None
        pending.server.close()


def _login_failed(pending: _Pending, code: str, now: datetime, message: str | None = None) -> dict:
    code = chatgpt.safe_code(code) or 'login_failed'
    with _agent_connection_tx() as conn:
        row = _load(conn, lock=True)
        _restore(conn, pending.prior, code, now, note=False)
        if row is not None and row['status'] in ('pending', *_PRIOR):
            conn.execute('UPDATE agent_connections SET last_error = %s WHERE id = 1', ((message or _message_for(code))[:300],))
    return {'ok': False, 'code': code}


def _finish_login(pending: _Pending, callback: dict, now: datetime) -> dict:
    if 'error' in callback:
        return _login_failed(pending, str(callback['error']), now)
    if now >= pending.expires_at:
        return _login_failed(pending, 'login_expired', now)
    code, client_id = callback.get('code'), callback.get('client_id')
    if not code or not client_id:
        return _login_failed(pending, 'registration_incomplete', now)
    with _TOKEN_LOCK:
        try:
            # a one-time code can fail to exchange after registration succeeded: keep the issued client_id first
            with _agent_connection_tx() as conn:
                _ensure_row(conn)
                conn.execute('UPDATE agent_connections SET client_id = %s, updated_at = %s WHERE id = 1',
                             (client_id, now))
                saved = _load(conn)
            fields = chatgpt.exchange_code(
                discovery=chatgpt.discover(), client_id=client_id, code=code, verifier=pending.verifier,
                redirect_uri=pending.redirect_uri, nonce=pending.nonce, subject=saved['subject'], now=now)
        except ChatGPTError as exc:
            return _login_failed(pending, exc.code, now, exc.message)
        except Exception as exc:        # noqa: BLE001 - nothing of a raw exception may reach the row or the log
            log.warning('chatgpt login failed: %s', type(exc).__name__)
            return _login_failed(pending, 'login_failed', now)
        with _agent_connection_tx() as conn:
            _load(conn, lock=True)
            expires = now + timedelta(seconds=fields['expires_in'])
            refresh_expires = now + REFRESH_TTL
            conn.execute(
                "UPDATE agent_connections SET status = 'connected', client_id = %s, subject = %s, email = %s, "
                'scopes = %s, access_token = %s, access_expires_at = %s, refresh_token = %s, '
                'refresh_expires_at = %s, earliest_refresh_at = %s, connected_at = %s, last_refresh_at = NULL, '
                'last_error = NULL, updated_at = %s WHERE id = 1',
                (client_id, fields['subject'], fields['email'] or saved['email'], fields['scopes'],
                 fields['access_token'], expires, fields['refresh_token'], refresh_expires,
                 fields['earliest_refresh_at'], now, now))
            _event(conn, 'connected', now, None, expires_at=expires.isoformat(),
                   refresh_expires_at=refresh_expires.isoformat(), scopes_ok=chatgpt.SHARING_SCOPE in fields['scopes'])
    return {'ok': True, 'code': None}


# ===================================================================================================================
# tokens
# ===================================================================================================================

def _invalidate(conn, code: str, now: datetime, message: str | None = None) -> None:
    """The connection can no longer be renewed: drop the tokens, ``needs_login``, one ``refresh_failed`` event."""
    conn.execute("UPDATE agent_connections SET status = 'needs_login', access_token = NULL, refresh_token = NULL, "
                 'access_expires_at = NULL, refresh_expires_at = NULL, earliest_refresh_at = NULL, '
                 'last_error = %s, updated_at = %s WHERE id = 1', ((message or _message_for(code))[:300], now))
    _event(conn, 'refresh_failed', now, code, reauth=True)


def access_token(*, now: datetime | None = None) -> str:
    """A usable access token (D32): with 300 s or more left it is used as is; otherwise, once ``earliest_refresh_at``
    has passed, it is refreshed under a row lock and the new tokens are committed before this returns.

    Raises ``ChatGPTError``: ``not_connected`` (no connection, or ``needs_login``); the refresh failure's own code
    (one of ``REAUTH_CODES`` also moves the row to ``needs_login`` and records the event; anything else leaves the
    status alone and only sets ``last_error``); ``refresh_not_ready`` (expired, but the issuer's earliest refresh
    time has not come)."""
    now = now or _utcnow()
    failure: ChatGPTError | None = None
    token: str | None = None
    with _TOKEN_LOCK:
        with _agent_connection_tx() as conn:
            row = _load(conn, lock=True)
            if row is None or row['status'] != 'connected':
                raise error_for('not_connected')
            remaining = (row['access_expires_at'] - now).total_seconds()
            earliest = row['earliest_refresh_at']
            if remaining >= REFRESH_THRESHOLD_S:
                token = row['access_token']
            elif earliest is not None and now < earliest:
                if remaining <= 0:
                    raise error_for('refresh_not_ready', retryable=True)
                token = row['access_token']           # nearly expired, but the issuer will not refresh yet
            else:
                try:
                    fields = chatgpt.refresh(
                        discovery=chatgpt.discover(), client_id=row['client_id'],
                        refresh_token=row['refresh_token'], previous_scope=' '.join(row['scopes'] or []),
                        subject=row['subject'], now=now)
                except ChatGPTError as exc:
                    failure = exc
                    if exc.code in REAUTH_CODES:
                        _invalidate(conn, exc.code, now, exc.message)
                    else:
                        conn.execute('UPDATE agent_connections SET last_error = %s, updated_at = %s WHERE id = 1',
                                     (exc.message[:300], now))
                else:
                    expires = now + timedelta(seconds=fields['expires_in'])
                    refresh_expires = now + REFRESH_TTL
                    conn.execute(
                        'UPDATE agent_connections SET access_token = %s, access_expires_at = %s, refresh_token = %s, '
                        'refresh_expires_at = %s, earliest_refresh_at = %s, scopes = %s, email = %s, '
                        'last_refresh_at = %s, last_error = NULL, updated_at = %s WHERE id = 1',
                        (fields['access_token'], expires, fields['refresh_token'], refresh_expires,
                         fields['earliest_refresh_at'], fields['scopes'], fields.get('email') or row['email'], now, now))
                    _event(conn, 'refreshed', now, None, expires_at=expires.isoformat(),
                           refresh_expires_at=refresh_expires.isoformat())
                    token = fields['access_token']
    if failure is not None:
        raise failure
    chatgpt.remember_secret(token)
    return token


def mark_needs_login(code: str, *, now: datetime | None = None) -> bool:
    """A model call proved the connection unusable (spec §8.8: 401 after a fresh refresh, ``invalid_token``, …): drop
    the tokens and go to ``needs_login``. True when a connected row was changed."""
    now = now or _utcnow()
    with _TOKEN_LOCK:
        with _agent_connection_tx() as conn:
            row = _load(conn, lock=True)
            if row is None or row['status'] != 'connected':
                return False
            _invalidate(conn, chatgpt.safe_code(code) or 'invalid_token', now)
    return True


def report_unauthorized(token: str, *, now: datetime | None = None) -> str:
    """A model call with ``token`` was answered 401 / ``invalid_token`` (spec §8.8 row 4). Returns what was done:

    - ``'marked'``: the token was obtained in the last ``FRESH_TOKEN_S`` seconds (login or refresh) and is still
      rejected, so the connection is unusable: ``needs_login`` + a ``refresh_failed`` event (``invalid_token``);
    - ``'refresh_forced'``: an older token: it is treated as expired, so the next ``access_token()`` refreshes it
      (nothing else is written; if that token is rejected as well the next report marks the row);
    - ``'ignored'``: the row no longer holds this token (already refreshed, disconnected, ...)."""
    now = now or _utcnow()
    with _TOKEN_LOCK:
        with _agent_connection_tx() as conn:
            row = _load(conn, lock=True)
            if row is None or row['status'] != 'connected' or row['access_token'] != token:
                return 'ignored'
            issued = max([t for t in (row['last_refresh_at'], row['connected_at']) if t is not None], default=None)
            if issued is not None and (now - issued).total_seconds() <= FRESH_TOKEN_S:
                _invalidate(conn, 'invalid_token', now)
                return 'marked'
            conn.execute('UPDATE agent_connections SET access_expires_at = %s, updated_at = %s WHERE id = 1', (now, now))
            return 'refresh_forced'


def first_failure_since_ok(*, since: datetime | None = None) -> bool:
    """True iff the connection is failed right now (``needs_login``), its latest re-login-class failure
    (``refresh_failed`` with ``reauth``) is at or after ``since`` (any time when ``since`` is None), and no
    ``connected`` / ``refreshed`` event came after that failure.

    A caller passes the start time of its previous run: each failure episode is then reported once, by the first run
    that starts after it. Transient refresh failures never count (they write no ``refresh_failed`` event)."""
    with _agent_connection_tx() as conn:
        row = _load(conn)
        events = conn.execute("SELECT kind, detail, created_at FROM agent_connection_events "
                              "WHERE kind IN ('connected', 'refreshed', 'refresh_failed') ORDER BY id DESC").fetchall()
    if row is None or row['status'] != 'needs_login':
        return False
    for kind, detail, created_at in events:
        if kind in ('connected', 'refreshed'):
            return False
        if (detail or {}).get('reauth'):
            return since is None or created_at >= since
    return False


def disconnect(*, now: datetime) -> dict:
    """Revoke the refresh token at the issuer (best effort) and clear the local credentials either way.

    ``host_id``, ``client_id`` and the chosen model stay; the account (subject / e-mail) is cleared, so the next login
    may use another ChatGPT account (spec §19: switching accounts = disconnect, then connect). ``{'revoked': bool}``:
    False when the issuer did not confirm, or when there was no token left to revoke."""
    global _PENDING
    with _PENDING_LOCK:
        pending, _PENDING = _PENDING, None
    if pending is not None:
        pending.server.close()
    with _TOKEN_LOCK:
        with _agent_connection_tx() as conn:
            row = _load(conn, lock=True)
            if row is None:
                return {'revoked': False}
            revoked = False
            if row['status'] == 'connected' and row['refresh_token']:
                try:
                    revoked = chatgpt.revoke(discovery=chatgpt.discover(), client_id=row['client_id'],
                                             refresh_token=row['refresh_token'])
                except ChatGPTError:
                    revoked = False
            conn.execute("UPDATE agent_connections SET status = 'disconnected', access_token = NULL, "
                         "refresh_token = NULL, access_expires_at = NULL, refresh_expires_at = NULL, "
                         "earliest_refresh_at = NULL, scopes = '{}', connected_at = NULL, last_refresh_at = NULL, "
                         'subject = NULL, email = NULL, last_error = NULL, updated_at = %s WHERE id = 1', (now,))
            _event(conn, 'disconnected', now, None, revoked=revoked)
    return {'revoked': revoked}


# ===================================================================================================================
# models
# ===================================================================================================================

def models() -> list[dict]:
    """The model list for the picker: ``[{'slug', 'display_name'}]`` (spec §8.3). ``not_connected`` when there is
    no usable connection."""
    return chatgpt.list_models(access_token())


def select_model(slug: str, *, now: datetime) -> dict:
    """Choose the model for the Agent; it must be in the current list. Returns ``{'model', 'model_display_name'}``."""
    chosen = next((item for item in models() if item['slug'] == slug), None)
    if chosen is None:
        raise error_for('model_not_listed')
    with _agent_connection_tx() as conn:
        _load(conn, lock=True)
        conn.execute('UPDATE agent_connections SET model = %s, model_display_name = %s, updated_at = %s WHERE id = 1',
                     (chosen['slug'], chosen['display_name'], now))
        _event(conn, 'model_selected', now, None, model=chosen['slug'])
    return {'model': chosen['slug'], 'model_display_name': chosen['display_name']}


# ===================================================================================================================
# read side
# ===================================================================================================================

def _mask_email(email: str | None) -> str | None:
    if not email or '@' not in email:
        return None
    name, domain = email.split('@', 1)
    return f'{name[:1]}***@{domain}'


def status_view(*, now: datetime | None = None) -> dict:
    """What the settings page may see (spec §8.9). Never a token, ``client_id``, ``host_id``, ``subject`` or the whole
    e-mail. ``status`` is the stored one (disconnected / pending / connected / needs_login); ``scopes_ok`` is False for
    a connected row whose account did not grant plan-usage sharing."""
    now = now or _utcnow()
    _expire_pending(now)
    with _agent_connection_tx() as conn:
        row = _heal(conn, now)
    with _PENDING_LOCK:
        pending = _PENDING
    row = row or {'status': 'disconnected', 'email': None, 'scopes': [], 'access_expires_at': None,
                  'refresh_expires_at': None, 'last_refresh_at': None, 'last_error': None, 'model': None,
                  'model_display_name': None}
    connected = row['status'] == 'connected'
    return {
        'status': 'pending' if pending is not None and row['status'] != 'connected' else row['status'],
        'email_masked': _mask_email(row['email']),
        'scopes_ok': _scopes_ok(row) if connected else False,
        'access_expires_at': _iso(row['access_expires_at']) if connected else None,
        'refresh_expires_at': _iso(row['refresh_expires_at']) if connected else None,
        'last_refresh_at': _iso(row['last_refresh_at']),
        'last_error': row['last_error'],
        'model': row['model'],
        'model_display_name': row['model_display_name'],
        'provider_setting': os.environ.get('WORKBENCH_AGENT_PROVIDER') or 'chatgpt',
        'pending': {'expires_at': pending.expires_at.isoformat()} if pending is not None else None,
    }


def read_for_settings() -> dict:
    """What ``settings_from_env`` needs: ``{'status', 'scopes_ok', 'model', 'secret'}``. ``secret`` is the stored access
    token, used only to scrub error texts; it must never be returned by an API. Raises when the table is unreadable."""
    now = _utcnow()
    _expire_pending(now)
    with _agent_connection_tx() as conn:
        row = _heal(conn, now)
    if row is None:
        return {'status': 'disconnected', 'scopes_ok': False, 'model': None, 'secret': None}
    return {'status': row['status'], 'scopes_ok': _scopes_ok(row), 'model': row['model'],
            'secret': row['access_token']}


# ===================================================================================================================
# CLI
# ===================================================================================================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m investment_assistant.core.llm.connection')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status', help='print the connection status (no tokens)')
    parser.parse_args(argv)
    try:
        view = status_view()
    except ChatGPTError as exc:
        print(f'{exc.code}: {exc.message}', file=sys.stderr)
        return 1
    except Exception as exc:            # noqa: BLE001 - the type is enough; the text could carry a connection string
        print(f'读取连接状态失败：{type(exc).__name__}', file=sys.stderr)
        return 1
    print(json.dumps(view, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
