"""Synthetic accounts for the demo, the sample renders and tests. No real account data."""
H, D = 3600, 86400
# key, alias, provider, [(window, used %, seconds until reset)]
SAMPLE = [
    ('claude_a', 'CLAUDE A', 'claude', [('WEEK', 86, 3 * D), ('5H', 41, 2 * H)]),
    ('claude_b', 'CLAUDE B', 'claude', [('5H', 52, 3 * H + 41 * 60), ('WEEK', 30, 5 * D)]),
    ('claude_c', 'CLAUDE C', 'claude', [('5H', 97, 38 * 60), ('WEEK', 64, 4 * D)]),
    ('codex_a', 'CODEX A', 'codex', [('5H', 34, 2 * H + 12 * 60)]),
    ('codex_b', 'CODEX B', 'codex', [('WEEK', 91, D + 4 * H)]),
    ('grok_a', 'GROK A', 'grok', [('BUDGET', 61, 6 * D + 2 * H)]),
    ('grok_b', 'GROK B', 'grok', [('BUDGET', 18, 12 * D + 3 * H)]),
]


def snapshot(now=None, sample=SAMPLE):
    accounts = {}
    for key, alias, provider, windows in sample:
        row = {'key': key, 'alias': alias, 'provider': provider, 'status': 'ok', 'identity_verified': True,
               'windows': [{'label': label, 'used_percent': used, 'resets_in': reset} for label, used, reset in windows]}
        if now is not None:
            row['fetched_at'] = row['last_success_at'] = now
            for window in row['windows']:
                window['resets_at'] = now + window['resets_in']
        accounts[key] = row
    return {'schema': 1, 'updated_at': now or 0, 'accounts': accounts}


def balance_snapshot(now=None):
    """Three-provider preview with a synthetic DeepSeek balance."""
    from token_tv.deepseek import normalize_balance
    data = snapshot(now, [SAMPLE[0], SAMPLE[3]])
    data['accounts']['deepseek_a'] = {
        'key': 'deepseek_a', 'alias': 'DEEPSEEK A', 'provider': 'deepseek', 'status': 'ok',
        'identity_verified': False, 'source': 'deepseek_api', 'windows': [],
        'fetched_at': now or 0, 'last_success_at': now or 0,
        'balance': normalize_balance({'is_available': True, 'balance_infos': [
            {'currency': 'USD', 'total_balance': '12.34', 'granted_balance': '2.00', 'topped_up_balance': '10.34'}]})}
    return data
