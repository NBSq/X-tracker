# x-narrative-tracker

Python MVP that watches selected crypto Twitter/X accounts, analyzes recent posts with OpenAI, stores structured results in SQLite, and sends Telegram alerts when a token or narrative gets unusually hot.

## What It Does

- Fetches latest posts from usernames in `data/accounts.json`
- Sends each new post to OpenAI for strict JSON analysis
- Extracts mentioned tokens, crypto narratives, sentiment, importance, and summary
- Stores analyzed posts in SQLite
- Calculates hype as:

```text
hype = mentions_count * average_importance
```

- Sends Telegram alerts when hype crosses the configured threshold
- Runs continuously every 15 minutes by default

## Project Structure

```text
app/
  main.py
  config.py
  sources/x_client.py
  ai/analyzer.py
  db/database.py
  alerts/telegram.py
  scoring/hype_score.py
data/
  accounts.json
  narratives.json
```

## Setup

Requires Python 3.11.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env`:

```text
X_BEARER_TOKEN=your_x_bearer_token
OPENAI_API_KEY=your_openai_api_key
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
```

Optional settings:

```text
DATABASE_PATH=x_narrative_tracker.sqlite3
OPENAI_MODEL=gpt-4o-mini
FETCH_INTERVAL_SECONDS=900
HYPE_ALERT_THRESHOLD=25
POSTS_PER_ACCOUNT=10
```

## Configure Sources

Update `data/accounts.json` with X usernames:

```json
{
  "accounts": ["elonmusk", "VitalikButerin", "a16zcrypto"]
}
```

Update `data/narratives.json` with narratives you care about:

```json
{
  "narratives": ["AI agents", "DePIN", "restaking"]
}
```

## Run

```bash
python -m app.main
```

The app loops forever. To test faster, set:

```text
FETCH_INTERVAL_SECONDS=60
HYPE_ALERT_THRESHOLD=5
```

## Notes

- X API v2 access and rate limits depend on your X developer plan.
- The app skips posts already stored by `post_id`.
- Alerts are de-duplicated for the same token or narrative within a 60-minute window.
- SQLite JSON queries use SQLite's built-in JSON functions, available in modern Python SQLite builds.
