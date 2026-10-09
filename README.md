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

## Buttons (bot.py)

`rss2gram.py` posts each new item (title + links) to `TELEGRAM_CHAT_ID` with a
**Summary** button. It does no LLM work itself. `bot.py` is a long-running
process that handles the button clicks:

* **Summary** - scrapes the article, asks Claude for location/category/keywords/
  4-sentence Russian summary, replies under the post (cached in the DB, so a
  second click is free). The reply carries a **Send to useful Germany** button.
* **Send to useful Germany** - copies the summary message to
  `TELEGRAM_USEFUL_GERMANY_CHAT_ID`, once per article.
* **Mention + link** - post a link in the chat and mention the bot
  (`@yourbot https://...`): it replies with the same summary (stored in the DB
  with `feed_url = 'manual'`, cached per link). In groups the bot must be a member;
  mentions reach it even with privacy mode on.

```
python rss2gram.py   # cron
python bot.py        # service, same env vars as above
```

### Run bot.py as a systemd service

`rss2gram-bot.service` is in the repo. It assumes user `vasa`, checkout in
`/home/vasa/rss2gram` and a venv in `.venv`; edit `User=`, `WorkingDirectory=`
and `ExecStart=` if yours differ. Secrets (`TELEGRAM_BOT_TOKEN`,
`TELEGRAM_USEFUL_GERMANY_CHAT_ID`, `ANTHROPIC_API_KEY`) are read from `.env`
next to the scripts.

```
sudo cp rss2gram-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rss2gram-bot
systemctl status rss2gram-bot
journalctl -u rss2gram-bot -f      # logs
```

After updating the code: `git pull && sudo systemctl restart rss2gram-bot`.
Run only one instance per bot token, otherwise Telegram returns a 409 error.
Keep the cron job for `rss2gram.py`.

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
sends a written, newspaper-style Russian digest to Telegram. `briefing.py` makes
one Claude call that returns Markdown: articles about the same story are merged
into one item with a single aggregated paragraph (all Sachsen-Anhalt election
coverage becomes one block, no repetition), grouped by country then topic, most
important to a Germany resident first. Source links trail each paragraph in
parentheses.

Run once a day from cron:
```
python digest.py                 # last 24h
python digest.py --hours 48
python digest.py --since 2026-09-06
python digest.py --no-llm         # skip Claude: flat country/category list, no prose
python digest.py --dry-run        # print instead of sending
```
Model: `DIGEST_MODEL` env var (default `claude-sonnet-5`). On any Claude failure
it falls back to the flat `--no-llm` layout. The digest is split across several
Telegram messages when long. Sends to `TELEGRAM_DIGEST_CHAT_ID`, falling back to
`TELEGRAM_CHAT_ID`.
