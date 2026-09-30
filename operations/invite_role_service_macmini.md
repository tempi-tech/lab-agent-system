# Invite Role Service on Mac mini

Standalone Discord entry service: join logging, welcome, and the entry panel.
It does not decide who holds the general member role (`agi-lab`); agi-backend
does (membership or a time-limited grant).

Canonical implementation:

- `src/services/invite_role_service/`
- entrypoint: `scripts/run_invite_role_service.py`

## What it does

- Connects to one Discord guild
- Snapshots invite uses on startup
- On member join:
  - does **not** assign the general role (`agi-lab`); agi-backend decides it
  - assigns the review role when invite detection fails
  - records the join and starts the welcome, regardless of role assignment
- Appends a local CSV join log
- Asks the Labochan OpenClaw agent to consider visible Discord context and send
  one welcome message in the member's onboarding thread (no role grant required)
- About one day later, checks the public self-introduction archive and sends one
  low-pressure follow-up in the member's onboarding thread only when no
  self-introduction is visible
- Serves the persistent entry panel (4 buttons) in the @everyone entry channel
  and relays requests to agi-backend (`entry` block, optional)
- Writes heartbeat and invite-baseline JSON files
- Periodically refreshes the invite baseline

## 1. Prepare config

Copy the example config and adjust only the values you need:

```bash
cp operations/invite_role_service.config.example.json operations/invite_role_service.config.json
```

Recommended:

- keep the token in the dedicated service environment via `INVITE_ROLE_DISCORD_TOKEN`
- when the `entry` block is used, also put `DISCORD_ENTRY_API_TOKEN` (same value
  as the agi-backend secret) in `.env.invite-role-service`
- keep all runtime files under `data/invite_role_service/`

## 2. Run manually now

```bash
cd /Users/kmini/lab-agent-system
export PYTHONPATH=.
set -a
source .env.invite-role-service
set +a
./.venv/bin/python scripts/run_invite_role_service.py --config operations/invite_role_service.config.json
```

Files to watch:

- `data/invite_role_service/service.log`
- `data/invite_role_service/heartbeat.json`
- `data/invite_role_service/invite_baseline.json`
- `data/invite_role_service/member_log.csv`

## 3. Enable via launchd

```bash
cp operations/com.kmini.invite-role-service.plist.example ~/Library/LaunchAgents/com.kmini.invite-role-service.plist
```

The wrapper script `scripts/start_invite_role_service.sh` automatically loads
`.env.invite-role-service`, so the plist does not need the token inline and
does not read the shared `.env`.

Then load it:

```bash
launchctl unload ~/Library/LaunchAgents/com.kmini.invite-role-service.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.kmini.invite-role-service.plist
launchctl start com.kmini.invite-role-service
```

Check status:

```bash
launchctl list | grep invite-role-service
cat /Users/kmini/lab-agent-system/data/invite_role_service/heartbeat.json
tail -n 50 /Users/kmini/lab-agent-system/data/invite_role_service/service.log
```

## 4. Course Pass (removed)

The Course Pass button, its persistent view, and the grant on the course invite
code were removed. Course participants now enter a passphrase from the entry
panel, and agi-backend grants time-limited access. An old config that still has
a `coursePass` block keeps loading; the block is ignored and a warning is
logged (`config key 'coursePass' is no longer supported and is ignored`).
You can delete the block from the real config at any time.

## 4b. Entry panel (entry channel)

Optional. Without the `entry` block nothing changes. With it, the service
re-registers a persistent view (fixed custom_ids, `timeout=None`) so the posted
panel keeps working after restarts, and welcome threads are created in
`entry.channelId` instead of `welcome.channelId`.

Buttons (custom_ids must not change once the panel is posted):

| Button | What happens |
|---|---|
| 「Discord 連携」 | link button to `entry.linkUrl` |
| 「講座に参加」 (`entry_panel:course_claim`) | modal with 合言葉 → `POST /internal/discord-entry/course-claims` → ephemeral reply |
| 「参加状態を確認」 (`entry_panel:status`) | `GET /internal/discord-entry/status/:id` → ephemeral reply |
| 「連携できない時はこちら」 (`entry_panel:support`) | modal with free text → posted to `entry.supportChannelId` (mentions disabled) → ephemeral 「運営にお伝えしました」 |

Config keys under `entry` (all optional; `0` means not set):

- `channelId` — the @everyone entry channel (create it in Discord first)
- `panelMessageId` — the posted panel message (optional; binds the view to it)
- `linkUrl` — default `https://agi-labo.com/settings`
- `backendBaseUrl` — default `https://api.agi-labo.com` (https only)
- `backendTokenEnv` — env var holding the bot-only API token, default
  `DISCORD_ENTRY_API_TOKEN`. When it is missing the buttons reply "準備中" and
  nothing is sent to the backend.
- `supportChannelId` — ops channel for 「連携できない時」
- `requestTimeoutSeconds` — default `10`

Logging: the passphrase, the support text, and the API token are never logged.
Course claims log `entry course_claim member=<id> status=<code> error=<code>`.

Post the panel once (HTTP only, no gateway session, so it does not disturb the
running service):

```bash
cd /Users/kmini/lab-agent-system
export PYTHONPATH=.
set -a; source .env.invite-role-service; set +a
./.venv/bin/python scripts/post_entry_panel.py --config operations/invite_role_service.config.json --dry-run
./.venv/bin/python scripts/post_entry_panel.py --config operations/invite_role_service.config.json
# put the printed id into entry.panelMessageId, then restart the service
# later text/button updates: add --edit
```

Expected startup logs:

- `registered entry panel persistent view message=...`
- `entry ready channel=... support_channel=... token_configured=True ...`

## 5. Labochan welcome

The `welcome` block lets the service post one join notice in the public join
notification channel, create a per-member onboarding thread, and ask the
Labochan OpenClaw agent to deliver a short welcome inside that thread after the
new member receives the general role. The service does not send a fixed welcome
template; it passes the member, thread, and operating rules to the agent. The
agent should inspect visible Discord context first, then write a short human
welcome. The service stores sent user ids and thread ids in
`welcome.statePath`, so a restart does not re-send the same greeting.

Important behavior:

- Do not treat missing search results as proof that a member has not introduced
  themselves.
- Do not ask for self-introduction if visible context or operator knowledge says
  the member has already introduced themselves.
- Never fall back to a fixed welcome template from the service layer.
- A follow-up is sent at most once. Members who introduced themselves, left the
  guild, are bots, or have no onboarding thread are recorded and skipped.
- If the Discord archive is missing or stale, the follow-up is deferred rather
  than guessing that no introduction exists.
- Follow-up copy keeps the guide and self-introduction links, but the agent may
  vary the wording and lightly reflect visible recent activity.

Config keys under `welcome`:

- `enabled` — turn the welcome message on/off
- `channelId` — public join-notification channel where starter messages appear
- `introChannelId` — optional self-introduction channel mention; for AGI Lab,
  this is the real self-introduction channel, not the welcome channel
- `statePath` — JSON state file for de-duplication
- `agentId` / `thinking` — Labochan agent and reasoning level used for the turn
- `openclawBin` / `openclawCwd` — OpenClaw CLI invocation
- `nodeBinDir` — Node.js bin directory prepended to the service subprocess PATH
- `timeoutSeconds` — maximum wait for one send
- `skipBots` — avoid greeting bot accounts
- `threaded` — create a per-member public onboarding thread when true
- `threadAutoArchiveMinutes` — thread auto-archive duration accepted by Discord
- `followup.enabled` — enable the one-day follow-up sweep
- `followup.delaySeconds` — delay after the first welcome (normally `86400`)
- `followup.checkIntervalSeconds` — sweep cadence (normally `900`)
- `followup.discrawlDbPath` — read-only public Discord archive used to verify
  whether the member already introduced themselves
- `followup.archiveMaxAgeSeconds` — maximum archive age; stale data causes a
  defer, never a reminder

Disable the feature by setting `welcome.enabled=false` or removing the
`welcome` block. Join logging stays untouched.

### Restart after code or config changes

```bash
launchctl kickstart -k "gui/$(id -u)/com.kmini.invite-role-service"
tail -f /Users/kmini/lab-agent-system/data/invite_role_service/service.log
```

## 6. Rollback

Two granularities:

- Disable only the entry panel: remove the `entry` block and restart. Welcome
  threads go back to `welcome.channelId`. The posted panel buttons stop
  responding until the block is restored.
- Disable only Labochan welcome, keep join logging running:
  set `welcome.enabled=false`, then
  `launchctl kickstart -k "gui/$(id -u)/com.kmini.invite-role-service"`.
- Disable the whole service:
  `launchctl unload ~/Library/LaunchAgents/com.kmini.invite-role-service.plist`.

Rollback leaves existing members and roles untouched.
