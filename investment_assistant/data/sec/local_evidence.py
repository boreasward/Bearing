"""Shared ownership/SEC-link checks for indexed originals and monitor attachments."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit


def owned_path(raw, root, ticker, accession, *, must_exist=True):
    path = Path(raw).resolve()
    if (not path.is_relative_to(root / ticker) or accession not in path.name
            or (must_exist and not path.is_file())):
        raise ValueError('本地原文缺失或证券归属无效')
    return str(path)


def verified_sec_url(raw, accession):
    url = urlsplit(raw or '')
    if (url.scheme == 'https' and url.hostname in {'www.sec.gov', 'sec.gov'} and not url.username
            and not url.password and not url.query and not url.fragment
            and re.fullmatch(r'/Archives/edgar/data/\d+/' + accession.replace('-', '') + r'/[^/]+', url.path)):
        return raw
    return ''


def monitor_metadata(state_dir):
    if not state_dir:
        return {}
    path = Path(state_dir) / 'state.json'
    if not path.exists():
        return {}
    state = json.loads(path.read_text())
    if state.get('version') != 1 or not isinstance(state.get('files'), dict):
        raise ValueError('invalid monitor metadata')
    return state['files']
