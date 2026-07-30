# pyalexalist

An unofficial client for the Amazon Alexa Lists API. Heavily inspired by [gkeepapi](https://github.com/kiwiz/gkeepapi)

*pyalexalist is not supported nor endorsed by Amazon.*



## Obtaining Alexa credentials

Alexa access uses session cookies obtained by signing in through a proxy login flow powered by [alexa-cookie-cli](https://github.com/adn77/alexa-cookie-cli).

### Step 1 — Create `service_auth.json`

Using `config/service_auth.json.example` as a reference, create `config/service_auth.json` and fill in your Alexa refresh token (leave it empty for now — you will get it in Step 2).

### Step 2 — Download the Alexa Cookie CLI

```
python -m pyalexalist.setup_alexa_cli
```

This fetches the latest release from [adn77/alexa-cookie-cli](https://github.com/adn77/alexa-cookie-cli/releases) and saves the right binary for your OS in `alexa-cookie-cli/`. Skips the download if a binary is already present.

Optional arguments:

| Argument | Description |
|---|---|
| `--list` / `-l` | List all available release versions and their descriptions |
| `--version VERSION` / `-v VERSION` | Download a specific version (e.g. `--version 5.0.1`) instead of latest |
| `--force` / `-f` | Re-download even if a binary already exists |

```
# list available versions
python -m pyalexalist.setup_alexa_cli --list

# download a specific version
python -m pyalexalist.setup_alexa_cli --version 5.0.1
```

### Step 3 — Sign in and get a refresh token

Run the CLI binary for your platform and region:

```
# Windows
.\alexa-cookie-cli\alexa-cookie-cli-win.exe -p amazon.com -b amazon.com -a en_US -L en-US

# Linux
./alexa-cookie-cli/alexa-cookie-cli-linux -p amazon.com -b amazon.com -a en_US -L en-US

# macOS
./alexa-cookie-cli/alexa-cookie-cli-macos -p amazon.com -b amazon.com -a en_US -L en-US
```

The `-a en_US -L en-US` flags force the sign-in page to English (defaults to German otherwise). After signing in, copy the refresh token from the output and paste it into `alexa.refresh_token` in `config/service_auth.json`.

### Step 4 — Exchange for session cookies (optional)

```
python -m pyalexalist.get_alexa_cookies
```

This reads `alexa.refresh_token` from `config/service_auth.json` and writes the resulting session cookies into `config/runtime_credentials.json`.

> **Note:** This step is optional. If `runtime_credentials.json` does not exist when the module starts, it will exchange the refresh token automatically. Run this step manually to pre-populate the cache or to force a cookie refresh outside of the app.

## Programmatic usage

```python
from pyalexalist import AlexaList

# Default — looks for config/service_auth.json relative to the current working directory
alexa = AlexaList()

# Explicit path — pass when service_auth.json is not at the default location
from pathlib import Path
alexa = AlexaList(service_auth_path=Path("/path/to/config/service_auth.json"))
```

`runtime_credentials.json` is used as an internal cookie cache and is written to the same directory as `service_auth.json` automatically. You do not need to manage it directly.

`AlexaAPI` (low-level client) also accepts a `runtime_creds_path` override if you need the cache file at a non-standard location.

## Session expiry

The Alexa session cookies have a finite lifetime. The module automatically checks expiry on each API call and will:

- **Warn** (`WARNING` log) when expiry is within 30 days, once per sync interval
- **Auto-refresh** when expired: exchanges `alexa.refresh_token` from `service_auth.json`
  for new cookies via Amazon's token endpoint, then reloads the session transparently
- **Raise** `AlexaSessionExpiredException` only if auto-refresh fails (e.g. `service_auth.json`
  not found, token invalid, or network error)

If auto-refresh fails and `cookie_expiry_retries` is set in `config/lists_sync_config.toml`,
the app will also wait and retry automatically, allowing you to refresh credentials manually
without restarting.

The only scenario requiring manual intervention is if the refresh token itself expires (e.g. after a long period of inactivity or an Amazon password change). In that case, re-run Steps 3 and 4:

```
# Step 3 — sign in again to get a new refresh token, then update service_auth.json
.\alexa-cookie-cli\alexa-cookie-cli-win.exe -p amazon.com -b amazon.com -a en_US -L en-US  # Windows
./alexa-cookie-cli/alexa-cookie-cli-linux -p amazon.com -b amazon.com -a en_US -L en-US    # Linux
./alexa-cookie-cli/alexa-cookie-cli-macos -p amazon.com -b amazon.com -a en_US -L en-US    # macOS

# Step 4 — force a cookie exchange with the new token (optional — app will also do this on next start)
python -m pyalexalist.get_alexa_cookies
```

`AlexaAPI` and `AlexaList` both accept a `service_auth_path` argument if `service_auth.json`
is not at the default location (`Path.cwd() / "config" / "service_auth.json"`).
