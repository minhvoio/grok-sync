# grok-sync

Multi-account **Grok / xAI SuperGrok** OAuth switcher + live usage monitor for OpenCode.

Two commands:

| Command | Role | Claude equivalent |
|---------|------|-------------------|
| `grok-sync` | login / switch / rotate accounts | `claude-sync` |
| `gu` | live SuperGrok usage bars | `cu` |

## Why

You already use OpenCode with xAI Grok OAuth (`opencode auth login` → xAI Grok).  
OpenCode only keeps **one** `xai` session at a time. `grok-sync` stores named accounts and switches the active one into OpenCode. `gu` prints your weekly SuperGrok pool + monthly credits without opening a browser.

## What you'll see

```
  Grok Usage  (work)  zeldacadwell95965@hotmail.com  -  2026-07-14 12:40
  ───────────────────────────────────────────────────────
  Weekly     ████░░░░░░░░░░░░░░░░   22.0%  resets in 6d10h
  Monthly    ███░░░░░░░░░░░░░░░░░   15.9%  3175 / 20000
  Api        ████░░░░░░░░░░░░░░░░   22.0%
```

Bars are green when there's headroom, yellow at 70%+, red at 90%+.

---

## Install

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/minhvoio/grok-sync/main/install.sh | bash
```

### Any platform (direct)

```bash
npm install -g github:minhvoio/grok-sync
```

### Local checkout

```bash
cd /path/to/grok-sync
npm install -g .
```

Requirements:

- Node.js >= 18 (command wrappers)
- Python 3
- curl
- OpenCode with at least one xAI Grok OAuth login

---

## Usage

### Multi-account (`grok-sync`)

```bash
# 1) Log into OpenCode with the account you want to save
opencode auth login
#    → pick "xAI Grok OAuth (SuperGrok Subscription)"
#    → finish browser / device-code flow

# 2) Snapshot it under a label
grok-sync --login personal

# 3) Log into another account, snapshot again
opencode auth login
grok-sync --login work

# 4) Switch which account OpenCode uses
grok-sync --switch work
grok-sync --list
grok-sync --status
grok-sync --rotate
grok-sync --sync          # re-write active + refresh if needed
grok-sync --remove work
```

Accounts live at:

```text
~/.config/opencode-grok-auth-sync/accounts.json
```

Switching writes into OpenCode:

```text
~/.local/share/opencode/auth.json   →  "xai": { type, access, refresh, expires }
```

### Usage (`gu`)

```bash
gu                 # active grok-sync account, else live OpenCode xai
gu work            # named account / profile
gu all             # every saved account
gu --json
gu --no-cache
gu save team-a     # snapshot current OpenCode tokens as a usage profile
gu list
```

Credential resolution order for `gu`:

1. Named profile: `~/.config/ai-usage-monitors/profiles/<name>/grok.json`
2. `grok-sync` store account with that name
3. Active `grok-sync` account
4. Live OpenCode `xai` session

### JSON shape (`gu --json`)

```json
{
  "weeklyPercent": 22.0,
  "weeklyPeriodStart": "2026-07-13T22:22:16.374927+00:00",
  "weeklyPeriodEnd": "2026-07-20T22:22:16.374927+00:00",
  "weeklyPeriodType": "USAGE_PERIOD_TYPE_WEEKLY",
  "productUsage": [{ "product": "Api", "usagePercent": 22.0 }],
  "monthlyLimit": 20000,
  "monthlyUsed": 3175,
  "monthlyPercent": 15.875,
  "email": "you@example.com"
}
```

---

## How it works

### Auth

xAI SuperGrok OAuth (same client OpenCode / Grok CLI use):

- Client ID: `b1a00492-073a-47ea-816f-4c329264a828`
- Token refresh: `POST https://auth.x.ai/oauth2/token`
- OpenCode stores tokens under provider key `xai`

`grok-sync --login` does **not** open a browser itself. It snapshots whatever OpenCode already has after you run `opencode auth login`. That keeps the OAuth client allowlist happy and matches how you already log in.

### Usage

Grok has no public “usage” doc endpoint for SuperGrok pools. The Grok CLI billing surface works with the same OAuth bearer:

```http
GET https://cli-chat-proxy.grok.com/v1/billing?format=credits   # weekly %
GET https://cli-chat-proxy.grok.com/v1/billing                  # monthly credits
GET https://cli-chat-proxy.grok.com/v1/user                     # email label
```

Responses are cached 90 seconds so repeated `gu` calls don't spam the API.

---

## Companion

Live Claude / Codex monitors live in a separate repo:

- [`ai-usage-monitors`](https://github.com/minhvoio/ai-usage-monitors) → `cu` / `cou`

Claude multi-account switcher on your machine is still the local `claude-sync` tool; this package is the Grok twin.

---

## License

MIT
