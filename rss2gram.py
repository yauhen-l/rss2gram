import feedparser
import telebot
import json
import traceback
from time import mktime
from datetime import datetime
import os
import socket

socket.setdefaulttimeout(30)

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

try:
    for url in data:
        print(url)
        last_time = datetime.fromisoformat(data[url])
        print("Last time {}".format(last_time))

        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print("Error happened on parsing feed " + url, e)
            bot.send_message(chat_id, "Failed to parse feed " + url + " " + e)
            continue
        for e in feed.entries[::-1]:
            e_time = datetime.fromtimestamp(mktime(e["published_parsed"]))
            e_link = e["link"]
            if e_time > last_time and e_link not in processed_items:
                print("Sending post from {}".format(e_time))
                msg_template = '*{title}* \n [LINK]({link})'
                if 'comments' in e:
                    msg_template =  msg_template + ' [COMMENTS]({comments})'
                msg = msg_template.format(**e)
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
