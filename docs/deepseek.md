# DeepSeek API balance

DeepSeek appears alongside Claude and Codex in the dashboard and all seven clock faces.
It shows money remaining, not a subscription usage percentage. The dashboard also shows
granted and topped-up balances for each reported currency (USD or CNY). The clock shows
USD when available, otherwise CNY.

The [official balance endpoint](https://api-docs.deepseek.com/api/get-user-balance/)
reports an account-wide balance and whether API access is available. It does not report
token usage, spending history, account identity, or spending attributable to one API key.
TokenTV makes only balance requests, never billable completion requests. It polls on the
same schedule as the other providers. Missing amounts stay unknown; failed refreshes keep
the last successful balance marked OLD. A reported zero remains zero.

## Set up

Create a new config; setup never overwrites an existing one. Include the providers you want:

```bash
token-tv setup --config ~/.config/token-tv/config-with-deepseek.json --yes \
  --claude-email you@example.com --codex-email you@example.com --deepseek \
  --device-url http://192.168.1.5
token-tv connect --config ~/.config/token-tv/config-with-deepseek.json --account deepseek_a
token-tv run --config ~/.config/token-tv/config-with-deepseek.json
```

Omit the other providers or device URL for a DeepSeek-only dashboard. Stop an existing
TokenTV process before starting the new config on the same dashboard port.

`connect` asks for the API key using hidden input in your own terminal, then checks its
balance. Do not paste keys into chat, config files, command arguments, or shell history.
On POSIX systems the saved `api-key` file has mode 600 in a directory with mode 700.
The config contains only the directory path; dashboard responses and clock images contain
no API key. The key stays on the computer running TokenTV and is sent only to the fixed
DeepSeek API endpoint; authenticated redirects are rejected.

Alternatively, supply `DEEPSEEK_API_KEY` through your process environment. It takes precedence
over a saved key and applies to every configured DeepSeek account. `connect` refuses to save
a key while that override is set. The variable is removed from provider CLI subprocesses.

Clock faces have three provider rows. With all four providers configured, the existing order
is Claude, Codex, Grok, DeepSeek, so DeepSeek does not fit on the clock. All four still appear
in the dashboard.

## Preview without an API key

```bash
token-tv demo --deepseek --out token-tv-deepseek-demo
```

This renders Claude, Codex, and a synthetic DeepSeek balance in every clock face.
