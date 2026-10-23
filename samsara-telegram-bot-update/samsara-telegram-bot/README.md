# Samsara → Telegram Fleet Alerts Bot

Python bot that posts Samsara safety events and alerts into a Telegram forum group (topics).

## Features

- Receives Samsara webhooks at `POST /webhook/samsara`
- Polls Samsara safety events (harsh events, AI camera events, media when available)
- Posts into Telegram topics (Speeding, Crash, Fault codes, …)
- Optional webhook signature verification
- Health check at `GET /health`

## 1. Local quick test

```bash
cd samsara-telegram-bot
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with your tokens
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Smoke test Telegram:

```bash
curl -X POST "http://127.0.0.1:8080/test/telegram"
```

## 2. Deploy on Railway (recommended)

1. Create account at https://railway.app
2. **New Project → Deploy from GitHub** (push this folder) **or** empty + Dockerfile
3. Set variables (Railway → Variables):

| Variable | Value |
|----------|--------|
| `SAMSARA_API_TOKEN` | your Samsara token |
| `TELEGRAM_BOT_TOKEN` | from BotFather |
| `TELEGRAM_CHAT_ID` | `-1003916220393` |
| `SAMSARA_WEBHOOK_SECRET` | (after creating webhook) |
| `TOPIC_MAP` | `{}` at first |
| `SAMSARA_BASE_URL` | `https://api.samsara.com` (or EU URL) |

4. Generate a public domain (Railway → Settings → Networking / Generate Domain)
5. Your Payload URL is:

```text
https://YOUR-RAILWAY-DOMAIN/webhook/samsara
```

6. In Samsara → **Add Webhook**:
   - Name: `Telegram Fleet Alerts`
   - Payload URL: the URL above
   - Event Subscriptions: pick Alert / Speeding / EngineFault / etc.
7. Copy the webhook **secret** into `SAMSARA_WEBHOOK_SECRET` and redeploy if needed
8. In Samsara **Alerts**, attach this webhook as a notification target

## 3. Topic mapping (optional but recommended)

When someone posts in a topic, `getUpdates` includes `message_thread_id`.

Or post once per topic and read the thread id, then set:

```env
TOPIC_MAP={"Speeding":2,"Crash":3,"HarshBraking":4,"BatteryVoltage":5,"FaultCodes":6,"ObstructedCamera":7,"General":1}
```

Keys the bot understands:

`Speeding`, `Crash`, `HarshBraking`, `HarshTurn`, `BatteryVoltage`, `FaultCodes`, `ObstructedCamera`, `Drowsy`, `FollowingDistance`, `MobileUsage`, `NoSeatbelt`, `RollingStop`, `General`

Unmapped events go to `General` if configured, otherwise the main group feed.

## 4. API token scopes (Samsara)

Enable at least:

- Read Safety Events & Scores
- Read Camera Media
- Read Vehicles / Drivers as needed
- Write Webhooks (only if managing webhooks via API)

## Notes

- Video may appear a few minutes after the event; the poller retries on later polls via event id dedupe for newly seen events only (first sight). For production you may extend media retry.
- Return 2xx quickly from the webhook; heavy work is already async/polling.
- Weekly PNG report boards can be added next as a scheduled job.
