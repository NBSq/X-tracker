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
- Includes a fully offline local MVP mode with fake posts and deterministic analysis
- Enriches spike alerts with top posts, related signals, confidence, and a watchlist action

## Project Structure

```text
app/
  main.py
  config.py
  sources/x_client.py
  sources/rss_client.py
  ai/analyzer.py
  db/database.py
  alerts/telegram.py
  scoring/hype_score.py
data/
  accounts.json
  narratives.json
  sample_posts.json
  rss_feeds.json
```

## Setup

Requires Python 3.11.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` before using live mode:

```text
X_BEARER_TOKEN=your_x_bearer_token
OPENAI_API_KEY=your_openai_api_key
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
```

Telegram credentials are optional. When omitted, spike alerts are printed to the console only.

Live-mode spike explanations are generated with OpenAI. Local mode produces a deterministic offline explanation so the complete alert workflow can be tested without API credentials.

Optional settings:

```text
DATABASE_PATH=x_narrative_tracker.sqlite3
OPENAI_MODEL=gpt-4o-mini
FETCH_INTERVAL_SECONDS=900
HYPE_ALERT_THRESHOLD=25
POSTS_PER_ACCOUNT=10
RSS_ARTICLES_PER_FEED=10
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

Live mode requires X and OpenAI credentials and loops every 15 minutes:

```bash
python -m app.main
```

RSS mode reads the public feeds configured in `data/rss_feeds.json`, analyzes new articles with OpenAI, and reuses the same SQLite, hype scoring, and Telegram alert pipeline:

```bash
python -m app.main --mode rss
```

RSS mode requires `OPENAI_API_KEY` but does not require `X_BEARER_TOKEN`. Individual feed failures are logged and do not stop other feeds from being processed.

RSS article authors are mapped to the shared post record's `username` field for compatibility with existing storage and alerts, and are also available through its `author` property.

Run RSS mode without an OpenAI key or OpenAI API calls:

```bash
python -m app.main --mode rss --mock-ai
```

`--mock-ai` uses deterministic keyword rules to detect common tokens and configured narratives, then generates sentiment, importance, spike explanations, confidence, and watchlist actions locally. It reuses the same SQLite, alert, and summary pipeline.

The app loops forever. To test faster, set:

```text
FETCH_INTERVAL_SECONDS=60
HYPE_ALERT_THRESHOLD=5
```

## Local MVP test

Local mode requires no X API or OpenAI API credentials. It reads 30 fake crypto posts from `data/sample_posts.json`, analyzes them with the built-in deterministic analyzer, stores results in SQLite, calculates hype scores, and prints spike alerts to the console.

```bash
copy .env.example .env
python -m app.main --mode local
```

Disable Telegram explicitly while keeping console alerts:

```bash
python -m app.main --mode local --no-telegram
```

To optionally send the same alerts to Telegram, set both values in `.env`:

```text
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
```

Local mode is a one-shot run. Posts and alerts are de-duplicated in SQLite, so use a fresh `DATABASE_PATH` or remove the test database when you want to replay every alert.

Reset all previous analyses and alerts before replaying the local MVP:

```bash
python -m app.main --mode local --reset-db
```

Print a summary report and optionally send it to Telegram:

```bash
python -m app.main --mode local --summary
```

Run the Telegram payload tests without sending a real message:

```bash
python -m unittest tests.test_telegram
```

## Notes

- X API v2 access and rate limits depend on your X developer plan.
- The app skips posts already stored by `post_id`.
- Alerts are de-duplicated for the same token or narrative within a 60-minute window.
- SQLite JSON queries use SQLite's built-in JSON functions, available in modern Python SQLite builds.
