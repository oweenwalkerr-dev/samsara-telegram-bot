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
| `TOPIC_MAP` | `{}` at first, then the value from `/setup/topics` |
| `DISPLAY_TIMEZONE` | `America/Chicago` |
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

## 3. Topics (automatic)

Turn on **Topics** for the Telegram group and make the bot an admin with **Manage Topics**.
The bot then creates one topic per alert type the first time it needs it
(Speeding, Crash, Harsh Braking, Following Distance, ...).

To create them all at once, open this once:

```text
https://YOUR-DOMAIN/setup/topics
```

It returns a `TOPIC_MAP` value. **Paste it into your host's environment variables and save.**
Without that, a redeploy forgets the topic ids and creates duplicates.

Unknown alert types go to the group's General topic; the logs show
`Unmapped safety label(s) ...` so you can add them to `LABEL_TO_TOPIC_KEY` in `app/formatters.py`.

## 4. API token scopes (Samsara)

Enable at least:

- Read Safety Events & Scores
- Read Camera Media
- Read Vehicles and Read Drivers (used to show unit numbers and driver names)
- Write Webhooks (only if managing webhooks via API)

## Notes

- Video may appear a few minutes after the event; the poller retries on later polls via event id dedupe for newly seen events only (first sight). For production you may extend media retry.
- Return 2xx quickly from the webhook; heavy work is already async/polling.
- Weekly PNG report boards can be added next as a scheduled job.
