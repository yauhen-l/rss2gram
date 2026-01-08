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

And `processed` file contains all URL that are already processed to eliminate duplicates.
