"""DeepSeek account balance; no completion requests or inferred usage percentages."""
import os
import re
import stat
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

BALANCE_URL = 'https://api.deepseek.com/user/balance'
KEY_FILE = 'api-key'


def money(value):
    """Keep decimal strings exact; absent or malformed money is unknown, never zero."""
    if not isinstance(value, str) or not re.fullmatch(r'-?\d{1,13}(?:\.\d{1,12})?', value):
        return None
    try:
        amount = Decimal(value)
        return format(amount, 'f') if amount.is_finite() else None
    except InvalidOperation:
        return None


def normalize_balance(data):
    if not isinstance(data, dict) or not isinstance(data.get('balance_infos'), list):
        raise ValueError('Invalid balance response')
    currencies = {}
    for item in data['balance_infos']:
        if not isinstance(item, dict) or item.get('currency') not in ('USD', 'CNY'):
            continue
        currency = item['currency']
        if currency in currencies:
            raise ValueError('Duplicate balance currency')
        currencies[currency] = {'currency': currency,
                                **{field: money(item.get(field)) for field in
                                   ('total_balance', 'granted_balance', 'topped_up_balance')}}
    return {'is_available': data.get('is_available') if isinstance(data.get('is_available'), bool) else None,
            'currencies': [currencies[c] for c in ('USD', 'CNY') if c in currencies]}


def has_balance(row):
    return any(item.get('total_balance') is not None for item in (row.get('balance') or {}).get('currencies', []))


def primary_balance(row):
    if row.get('status') not in ('ok', 'stale'):
        return None
    return next((item for item in (row.get('balance') or {}).get('currencies', [])
                 if item.get('total_balance') is not None), None)


def amount_text(value):
    if value is None:
        return '--'
    amount = Decimal(value)
    if 0 < abs(amount) < Decimal('0.01'):
        return '-<0.01' if amount < 0 else '<0.01'
    if abs(amount) >= 1000000:
        return f'{amount / 1000000:.2f}M'
    return f'{amount:.2f}'


def key_path(account):
    return Path(account['source_home']).expanduser() / KEY_FILE


def valid_key(value):
    return isinstance(value, str) and 8 <= len(value) <= 512 and all(33 <= ord(c) <= 126 for c in value)


def api_key(account):
    from token_tv.sources import SourceError
    value = os.environ.get('DEEPSEEK_API_KEY')
    if value is None:
        path = key_path(account)
        # Avoid symlink surprises and world/group-readable secret files on POSIX.
        with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))) as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or (os.name == 'posix' and info.st_mode & 0o077):
                raise SourceError('unsafe_key_permissions')
            value = handle.read(514).strip()
    if not valid_key(value):
        raise SourceError('auth_required')
    return value


def save_key(account, value):
    """Called only by the local, hidden-input connect command; never put keys in config."""
    if not valid_key(value):
        raise ValueError('Invalid API key format')
    path = key_path(account)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix='.api-key-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(value + '\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def payload(account):
    from token_tv.sources import json_get
    return json_get(BALANCE_URL, {'Authorization': 'Bearer ' + api_key(account), 'Accept': 'application/json'})
