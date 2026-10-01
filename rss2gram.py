import feedparser
import telebot
from telebot import types
import requests
import enrich as enrich_mod
import store
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import json
import traceback
from time import mktime
from datetime import datetime
import os

config = "/home/vasa/rss2gram/config.json"

conn = store.connect()
processed_items = store.seen_links(conn)

data = json.load( open( config) )

token = os.getenv('TELEGRAM_BOT_TOKEN')
chat_id = os.getenv('TELEGRAM_CHAT_ID')
bot = telebot.TeleBot(token, parse_mode="MARKDOWN")

session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retries)
session.mount("https://", adapter)
session.mount("http://", adapter)

try:
    for url in data:
        print(url)
        last_time = datetime.fromisoformat(data[url])
        print("Last time {}".format(last_time))

        try:
            response = session.get(url, timeout=10)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
        except Exception as e:
            print("Error happened on parsing feed " + url, e)
            bot.send_message(chat_id, "Failed to parse feed " + url + " " + str(e))
            continue
        for e in feed.entries[::-1]:
            e_time = datetime.fromtimestamp(mktime(e["published_parsed"]))
            e_link = e["link"]
            if e_time > last_time and e_link not in processed_items:
                print("Sending post from {}".format(e_time))
                links = '[LINK]({link})'.format(**e)
                if 'comments' in e:
                    links += ' [COMMENTS]({comments})'.format(**e)

                msg = '*{title}* \n '.format(**e) + links
                print(msg)

                article_id = store.record(
                    conn,
                    link=e_link,
                    feed_url=url,
                    title=e.get("title", ""),
                    published=e_time.isoformat(),
                    feed_text=enrich_mod.feed_text(e),
                    sent_chat=str(chat_id),
                )
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("Summary", callback_data="sum:{}".format(article_id)))
                try:
                    bot.send_message(chat_id, msg, reply_markup=markup)
                except Exception:
                    store.delete(conn, article_id)  # retry on next run
                    raise
                last_time = e_time
                processed_items.add(e_link)
            data[url] = "{}".format(last_time)
except Exception as e:
    print("Error happened ", e)
    traceback.print_exc()
finally:
    json.dump(data, open( config, 'w' ))
    conn.close()
