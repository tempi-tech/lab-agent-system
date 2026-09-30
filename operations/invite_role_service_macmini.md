# Invite Role Service on Mac mini

This is the smallest standalone recovery path for role assignment.

Canonical implementation:

- `src/services/invite_role_service/`
- entrypoint: `scripts/run_invite_role_service.py`

## What it does

- Connects to one Discord guild
- Snapshots invite uses on startup
- On member join:
  - always assigns the general role
  - assigns the review role when invite detection fails
- Re-registers the Course Pass persistent button view for the existing panel message
- Appends a local CSV join log
- Asks the Labochan OpenClaw agent to consider visible Discord context and send
  one welcome message in the Labochan room after a successful general-role grant
- About one day later, checks the public self-introduction archive and sends one
  low-pressure follow-up in the member's onboarding thread only when no
  self-introduction is visible
- Writes heartbeat and invite-baseline JSON files
- Periodically refreshes the invite baseline

## 1. Prepare config

Copy the example config and adjust only the values you need:

```bash
cp operations/invite_role_service.config.example.json operations/invite_role_service.config.json
```

Recommended:

- keep the token in the dedicated service environment via `INVITE_ROLE_DISCORD_TOKEN`
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

Expected startup logs for Course Pass:

- `registered course pass persistent view ...`
- `course pass ready ... manage_roles=True hierarchy_ok=True component_found=True`

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

## 4. Course Pass button

The service registers a persistent `discord.ui.View` bound to the existing panel
message and handles the Course Pass role claim button. **It does not post or edit
the panel message.**

Config keys under `coursePass` in `operations/invite_role_service.config.json`:

- `roleId` — role granted on click
- `panelChannelId` — panel message channel id (optional; used for startup validation only)
- `panelMessageId` — `client.add_view(view, message_id=...)` target
- `customId` — button custom_id to match (e.g. `course_pass_role:claim:2026-04`)
- `buttonLabel` / `openChannelLabel` / `openChannelUrl` — cosmetic / link-button

Disable the feature by removing the `coursePass` block; the rest of the service
(invite→agi-lab role grant, course invite exclusions) is untouched.

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
`welcome` block. Role assignment and Course Pass stay untouched.

### Per-click log line

```
course_pass outcome=granted member=<id> role=<role_id> detail=granted
```

Possible outcomes: `granted`, `already_has_role`, `wrong_guild`, `role_not_found`,
`member_unresolved`, `missing_manage_roles`, `hierarchy_blocked`.

### Restart after code or config changes

```bash
launchctl kickstart -k "gui/$(id -u)/com.kmini.invite-role-service"
tail -f /Users/kmini/lab-agent-system/data/invite_role_service/service.log
```

### MacBook Pro cutover

- Button interactions route to the bot **application** that posted the panel,
  not the process. Mac mini receives clicks only if it uses the same bot token
  as whatever posted the panel. On boot verify `component_found=True`; then
  confirm one real click produces `outcome=granted`.
- As long as MBP and Mac mini share the token, only one holds the gateway
  session at a time — Discord kicks the older one on each new `IDENTIFY`.
  Safe cutover order:
  1. Verify Mac mini `course pass ready ... =True` + one `outcome=granted`
  2. Stop the MBP bot process (or disable Course Pass handling there)
  3. Watch Mac mini heartbeat stay `status=ready` for a few minutes

## 6. Rollback

Two granularities:

- Disable only Labochan welcome, keep invite→role grant running:
  set `welcome.enabled=false`, then
  `launchctl kickstart -k "gui/$(id -u)/com.kmini.invite-role-service"`.
- Disable only Course Pass, keep invite→role grant running:
  remove the `coursePass` block from `operations/invite_role_service.config.json`,
  then `launchctl kickstart -k "gui/$(id -u)/com.kmini.invite-role-service"`.
- Disable the whole service:
  `launchctl unload ~/Library/LaunchAgents/com.kmini.invite-role-service.plist`.

Either rollback leaves existing members and the panel message untouched.
