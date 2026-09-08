# Description

Python script to check RSS feeds and send new items to Telegram channel.

# How to run

First you neew to create you Telegram Bot with BotFather: https://core.telegram.org/bots/features#botfather

Then you need to add your bot to a channel and read any message from it to know your channel ID.

And finally run:
```
> python -m venv .
> . .venv/bin/activate
> pip install -r requirements.txt
> TELEGRAM_BOT_TOKEN=${YOUR_TOKEN_HERE} TELEGRAM_CHAT_ID=${YOUR_CHAT_ID} python rss2gram.py
```

Config is in fomrat:
```
{
  "RSS_FEED_URL": "LAST_UPDATED_TIME"
...
}
```

## Article store

Sent articles are written to a local SQLite database `articles.db` (next to the
scripts): link, feed, title, published date, category, location, keywords,
practical-impact flag and the chat it was sent to. The `link` column also
replaces the old flat `processed` file for de-duplication.

To migrate an existing `processed` file into the database once:
```
python migrate_processed.py [path-to-processed]
```

## Daily digest

`digest.py` takes the stored articles over a time window (default: last 24h) and
sends a briefing to Telegram. `briefing.py` makes one Claude call that returns a
country-first structure: grouped by the country the news is about, split into
story sections (all Sachsen-Anhalt election articles become one section, etc.),
each section carrying a 2–5 sentence Russian summary synthesised from the member
articles' own stored summaries. Source links are listed compactly after each
summary, not as the main content.

Run once a day from cron:
```
python digest.py                 # last 24h
python digest.py --hours 48
python digest.py --since 2026-09-06
python digest.py --no-llm         # skip Claude: flat country/category grouping, no summaries
python digest.py --dry-run        # print instead of sending
```
Model: `DIGEST_MODEL` env var (default `claude-sonnet-5`). On any Claude/JSON
failure it falls back to the flat `--no-llm` layout. Sends to
`TELEGRAM_DIGEST_CHAT_ID`, falling back to `TELEGRAM_CHAT_ID`.
