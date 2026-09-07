import feedparser
import telebot
import requests
from enrich import enrich
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
useful_chat_id = os.getenv('TELEGRAM_USEFUL_GERMANY_CHAT_ID')
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
                target_chat_id = chat_id
                res = None

                try:
                    res = enrich(e)
                    info = res.info
                    parts = (info.location.country, info.location.region, info.location.city)
                    loc = ", ".join(p for p in parts if p) or "—"
                    flag = "✅" if info.practical_impact else "➖"
                    scrape_note = "" if res.scraped else "\n\U000026A0 summary from RSS teaser only"
                    kw = ", ".join(info.keywords)
                    kw_line = "\n\U0001F511 {}".format(kw) if kw else ""
                    msg = "*{title}*\n{flag} \U0001F4CD {loc} | \U0001F3F7 {cat}{note}{kw_line}\n\n{summary}\n\n{links}".format(
                        title=e["title"],
                        flag=flag,
                        loc=loc,
                        cat=info.category,
                        note=scrape_note,
                        kw_line=kw_line,
                        summary=info.summary_ru,
                        links=links,
                    )
                    if info.practical_impact and useful_chat_id:
                        target_chat_id = useful_chat_id
                except Exception as ex:
                    print("Enrich failed for " + e_link, ex)

                print(msg)
                bot.send_message(target_chat_id, msg)
                last_time = e_time
                processed_items.add(e_link)

                info = res.info if res else None
                store.record(
                    conn,
                    link=e_link,
                    feed_url=url,
                    title=e.get("title", ""),
                    published=e_time.isoformat(),
                    category=getattr(info, "category", None),
                    location=getattr(info, "location", None),
                    summary_ru=getattr(info, "summary_ru", None),
                    practical_impact=getattr(info, "practical_impact", None),
                    impact_reason=getattr(info, "impact_reason", None),
                    scraped=getattr(res, "scraped", None),
                    keywords=getattr(info, "keywords", ()) or (),
                    sent_chat=str(target_chat_id),
                )
            data[url] = "{}".format(last_time)
except Exception as e:
    print("Error happened ", e)
    traceback.print_exc()
finally:
    json.dump(data, open( config, 'w' ))
    conn.close()
