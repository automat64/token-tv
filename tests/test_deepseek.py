import copy
import io
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from PIL import Image

from token_tv import cli, connect
from token_tv.deepseek import (BALANCE_URL, amount_text, api_key, has_balance, key_path,
                               normalize_balance, primary_balance, save_key)
from token_tv.display import STYLES, balance_panel, overview_rows, render_page, watch_payload
from token_tv.sample import balance_snapshot
from token_tv.sources import SourceError, fetch_account, load_config, scoped_env
from token_tv.state import UsageStore

PAYLOAD = {'is_available': True, 'balance_infos': [
    {'currency': 'USD', 'total_balance': '12.3456', 'granted_balance': '2.0000', 'topped_up_balance': '10.3456'}]}
ACCOUNT = {'key': 'deepseek_a', 'alias': 'DEEPSEEK A', 'provider': 'deepseek', 'source_home': '/unused'}
KEY = 'FAKE-SECRET-FOR-TESTS'


class DeepSeekTests(unittest.TestCase):
    def test_normalization_keeps_exact_currency_amounts_and_zero(self):
        data = copy.deepcopy(PAYLOAD)
        data['balance_infos'].append({'currency': 'CNY', 'total_balance': '0', 'granted_balance': None})
        data['secret'] = KEY
        result = normalize_balance(data)
        self.assertEqual(result['currencies'][0]['total_balance'], '12.3456')
        self.assertEqual(result['currencies'][1]['total_balance'], '0')
        self.assertIsNone(result['currencies'][1]['granted_balance'])
        self.assertIsNone(result['currencies'][1]['topped_up_balance'])
        self.assertNotIn(KEY, json.dumps(result))

    def test_invalid_and_missing_amounts_are_not_zero(self):
        for value in (None, True, 12, 'NaN', 'Infinity', '1e999', 'garbage', '9' * 100):
            with self.subTest(value=value):
                data = {'balance_infos': [{'currency': 'USD', 'total_balance': value}]}
                result = normalize_balance(data)
                self.assertIsNone(result['currencies'][0]['total_balance'])
                self.assertFalse(has_balance({'balance': result}))
        with self.assertRaises(ValueError):
            normalize_balance({'balance_infos': PAYLOAD['balance_infos'] * 2})
        with self.assertRaises(ValueError):
            normalize_balance({})
        self.assertEqual(amount_text('0'), '0.00')
        self.assertEqual(amount_text('0.001'), '<0.01')
        self.assertEqual(amount_text(None), '--')

    def test_fetch_uses_balance_endpoint_without_claiming_identity(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': KEY}), \
                patch('token_tv.sources.json_get', return_value=dict(PAYLOAD, access_token=KEY)) as get:
            row = fetch_account(ACCOUNT)
        get.assert_called_once_with(BALANCE_URL, {'Authorization': 'Bearer ' + KEY, 'Accept': 'application/json'})
        self.assertEqual(row['status'], 'ok')
        self.assertFalse(row['identity_verified'])
        self.assertEqual(row['windows'], [])
        self.assertNotIn(KEY, json.dumps(row))
        self.assertNotIn('source_home', row)

    def test_errors_are_sanitized_and_do_not_fabricate_balances(self):
        for error, status in ((HTTPError(BALANCE_URL, 401, KEY, {}, None), 'auth_required'),
                              (HTTPError(BALANCE_URL, 429, KEY, {}, None), 'rate_limited'),
                              (ValueError(KEY), 'error')):
            with self.subTest(status=status), patch('token_tv.sources.deepseek_payload', side_effect=error):
                row = fetch_account(ACCOUNT)
                self.assertEqual(row['status'], status)
                self.assertFalse(has_balance(row))
                self.assertNotIn(KEY, json.dumps(row))
        with patch('token_tv.sources.deepseek_payload', return_value={'balance_infos': []}):
            self.assertEqual(fetch_account(ACCOUNT)['status'], 'quota_unavailable')

    def test_poll_failure_retains_balance_as_old_and_recovers(self):
        with patch('token_tv.sources.deepseek_payload', return_value=PAYLOAD):
            good = fetch_account(ACCOUNT)
        failed = dict(ACCOUNT, status='error', windows=[], fetched_at=time.time(), identity_verified=False)
        replies = [good, failed, good]
        store = UsageStore([ACCOUNT], fetch=lambda _: copy.deepcopy(replies.pop(0)))
        store.refresh()
        store.refresh()
        row = store.snapshot()['accounts']['deepseek_a']
        self.assertEqual(row['status'], 'stale')
        self.assertEqual(row['balance'], good['balance'])
        self.assertIsNone(row['session_percent'])
        self.assertIn('OLD', watch_payload(store.snapshot())['accounts']['deepseek_a']['text'])
        store.refresh()
        self.assertEqual(store.snapshot()['accounts']['deepseek_a']['status'], 'ok')
        with patch('token_tv.state.time.time', return_value=good['fetched_at'] + 901):
            self.assertEqual(store.snapshot()['accounts']['deepseek_a']['status'], 'stale')

    def test_key_storage_is_private_and_never_inherited_by_other_clis(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            account = dict(ACCOUNT, source_home=str(Path(folder) / 'deepseek'))
            save_key(account, KEY)
            self.assertEqual(api_key(account), KEY)
            self.assertEqual(stat.S_IMODE(key_path(account).stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(key_path(account).parent.stat().st_mode), 0o700)
            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': KEY + '-ENV'}):
                self.assertEqual(api_key(account), KEY + '-ENV')
                self.assertNotIn('DEEPSEEK_API_KEY', scoped_env('codex', '/unused'))
            if os.name == 'posix':
                key_path(account).chmod(0o644)
                with self.assertRaises(SourceError):
                    api_key(account)
            for value in ('', KEY + '\nInjected: header', 'x' * 513):
                with self.assertRaises(ValueError):
                    save_key(account, value)

    def test_setup_and_config_reject_inline_keys_and_fake_identity(self):
        with tempfile.TemporaryDirectory() as folder, patch('sys.stdout', new_callable=io.StringIO):
            path = Path(folder) / 'config.json'
            self.assertEqual(cli.main(['setup', '--yes', '--deepseek', '--config', str(path)]), 0)
            account = load_config(path)['accounts'][0]
            self.assertEqual(account['provider'], 'deepseek')
            self.assertNotIn('email', account)
            original = path.read_bytes()
            with patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(cli.main(['setup', '--yes', '--deepseek', '--config', str(path)]), 1)
            self.assertEqual(path.read_bytes(), original)
            for field in ('api_key', 'email', 'snapshot_file', 'refresh_with_cli'):
                path.write_text(json.dumps({'accounts': [dict(account, **{field: KEY})]}))
                with self.assertRaises(ValueError):
                    load_config(path)

    def test_connect_uses_hidden_input_and_never_echoes_key(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            account = dict(ACCOUNT, source_home=str(Path(folder) / 'home'))
            config = Path(folder) / 'config.json'
            config.write_text(json.dumps({'accounts': [account]}))
            with patch('sys.argv', ['connect', '--config', str(config), '--account', 'deepseek_a']), \
                    patch('sys.stdin.isatty', return_value=True), patch('getpass.getpass', return_value=KEY), \
                    patch('token_tv.sources.deepseek_payload', return_value=PAYLOAD), \
                    patch('sys.stdout', new_callable=io.StringIO) as out:
                connect.main()
            self.assertEqual(api_key(account), KEY)
            self.assertNotIn(KEY, out.getvalue())
            self.assertIn('does not report account identity', out.getvalue())

    def test_all_faces_render_balance_missing_zero_and_stale(self):
        data = balance_snapshot(time.time())
        self.assertEqual([r['provider'] for r in overview_rows(data)], ['claude', 'codex', 'deepseek'])
        row = data['accounts']['deepseek_a']
        for style in STYLES:
            frames = []
            for state, amount in [('ok', '12.34'), ('ok', '0.00'), ('stale', '12.34'), ('auth_required', None)]:
                row['status'] = state
                row['balance']['currencies'][0]['total_balance'] = amount
                body = render_page(data, style=style)
                with Image.open(io.BytesIO(body)) as image:
                    self.assertEqual(image.size, (240, 240))
                self.assertLess(len(body), 400000 if style == 'space' else 60000)
                frames.append(body)
            self.assertEqual(len(set(frames)), 4, style)

    def test_balance_panel_has_currency_and_no_percent_or_reset(self):
        row = balance_snapshot()['accounts']['deepseek_a']
        image = Image.new('RGB', (240, 240))
        with patch('PIL.ImageDraw.ImageDraw.text') as text:
            balance_panel(image, row, 3)
        labels = ' '.join(call.args[1] for call in text.call_args_list)
        self.assertIn('12.34', labels)
        self.assertIn('USD REMAINING', labels)
        self.assertNotIn('%', labels)
        self.assertNotIn('RESET', labels)
        row['status'] = 'identity_mismatch'
        self.assertIsNone(primary_balance(row))


if __name__ == '__main__':
    unittest.main()
