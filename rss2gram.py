import feedparser
import telebot
import requests
from enrich import enrich
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import json
import traceback
from time import mktime
from datetime import datetime
import os

config = "/home/vasa/rss2gram/config.json"
processed_file = "/home/vasa/rss2gram/processed"
processed_items = set()

with open(processed_file) as file:
    for line in file:
        processed_items.add(line.rstrip())

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

                try:
                    res = enrich(e)
                    info = res.info
                    parts = (info.location.country, info.location.region, info.location.city)
                    loc = ", ".join(p for p in parts if p) or "—"
                    flag = "✅" if info.practical_impact else "➖"
                    scrape_note = "" if res.scraped else "\n\U000026A0 summary from RSS teaser only"
                    msg = "*{title}*\n{flag} \U0001F4CD {loc} | \U0001F3F7 {cat}{note}\n\n{summary}\n\n{links}".format(
                        title=e["title"],
                        flag=flag,
                        loc=loc,
                        cat=info.category,
                        note=scrape_note,
                        summary=info.summary_ru,
                        links=links,
                    )
                except Exception as ex:
                    print("Enrich failed for " + e_link, ex)

                print(msg)
                bot.send_message(chat_id, msg)
                last_time = e_time
                processed_items.add(e_link)
                with open(processed_file, "a") as pf:
                    pf.write(e_link + '\n')
            data[url] = "{}".format(last_time)
except Exception as e:
    print("Error happened ", e)
    traceback.print_exc()
finally:
    json.dump(data, open( config, 'w' ))
