"""Long-running bot that handles the inline buttons on posts from rss2gram.py.

  * "Summary"               - enrich the article on demand and reply with the summary;
  * "Send to useful Germany" - forward the article to TELEGRAM_USEFUL_GERMANY_CHAT_ID.

Run it as a service (rss2gram.py stays a cron job):
    python bot.py
"""

import os
import threading
from pathlib import Path
from types import SimpleNamespace

import telebot
from dotenv import load_dotenv
from telebot import types

import store
from enrich import enrich

load_dotenv(Path(__file__).with_name(".env"))

token = os.getenv("TELEGRAM_BOT_TOKEN")
useful_chat_id = os.getenv("TELEGRAM_USEFUL_GERMANY_CHAT_ID")
bot = telebot.TeleBot(token, parse_mode="MARKDOWN")

_in_progress = set()  # article ids being summarised right now
_lock = threading.Lock()


def article_links(article) -> str:
    return "[LINK]({})".format(article["link"])


def summary_text(article) -> str:
    country = article["country"]
    parts = (country, article["region"], article["city"])
    loc = ", ".join(p for p in parts if p) or "—"
    flag = "✅" if article["practical_impact"] else "➖"
    note = "" if article["scraped"] else "\n\U000026A0 summary from RSS teaser only"
    kw = ", ".join(r[0] for r in article["_keywords"])
    kw_line = "\n\U0001F511 {}".format(kw) if kw else ""
    return "*{title}*\n{flag} \U0001F4CD {loc} | \U0001F3F7 {cat}{note}{kw_line}\n\n{summary}\n\n{links}".format(
        title=article["title"],
        flag=flag,
        loc=loc,
        cat=article["category"],
        note=note,
        kw_line=kw_line,
        summary=article["summary_ru"],
        links=article_links(article),
    )


def summary_markup(article):
    if not useful_chat_id or article["forwarded"]:
        return None
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("Send to useful Germany", callback_data="ug:{}".format(article["id"]))
    )
    return markup


def load(conn, article_id):
    article = store.get(conn, article_id)
    if article:
        article["_keywords"] = conn.execute(
            "SELECT keyword FROM keywords WHERE article_id = ? ORDER BY keyword", (article_id,)
        ).fetchall()
    return article


def send(chat_id, text, **kwargs):
    """Send with Markdown, falling back to plain text if Telegram rejects the markup."""
    try:
        return bot.send_message(chat_id, text, **kwargs)
    except telebot.apihelper.ApiTelegramException as ex:
        if "parse entities" not in str(ex):
            raise
        return bot.send_message(chat_id, text, parse_mode=None, **kwargs)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("sum:"))
def on_summary(call):
    article_id = int(call.data.split(":", 1)[1])
    msg = call.message

    with _lock:
        if article_id in _in_progress:
            bot.answer_callback_query(call.id, "Already working on it")
            return
        _in_progress.add(article_id)
    bot.answer_callback_query(call.id, "Summarising…")

    conn = store.connect()
    try:
        article = load(conn, article_id)
        if not article:
            bot.send_message(msg.chat.id, "Article not found in the database", reply_to_message_id=msg.message_id)
            return

        if not article["summary_ru"]:
            entry = SimpleNamespace(
                title=article["title"], link=article["link"], summary=article["feed_text"] or ""
            )
            try:
                res = enrich(entry)
            except Exception as ex:  # noqa: BLE001
                print("Enrich failed for " + article["link"], ex)
                bot.send_message(
                    msg.chat.id, "Failed to summarise: {}".format(ex), reply_to_message_id=msg.message_id
                )
                return
            store.save_enrichment(conn, article_id, res.info, res.scraped)
            article = load(conn, article_id)

        send(
            msg.chat.id,
            summary_text(article),
            reply_to_message_id=msg.message_id,
            reply_markup=summary_markup(article),
            disable_web_page_preview=True,
        )
        # The summary exists now - drop the button from the original post.
        try:
            bot.edit_message_reply_markup(msg.chat.id, msg.message_id, reply_markup=None)
        except telebot.apihelper.ApiTelegramException:
            pass
    finally:
        conn.close()
        with _lock:
            _in_progress.discard(article_id)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("ug:"))
def on_forward(call):
    article_id = int(call.data.split(":", 1)[1])
    if not useful_chat_id:
        bot.answer_callback_query(call.id, "TELEGRAM_USEFUL_GERMANY_CHAT_ID is not set")
        return

    conn = store.connect()
    try:
        article = load(conn, article_id)
        if not article:
            bot.answer_callback_query(call.id, "Article not found")
            return
        if article["forwarded"]:
            bot.answer_callback_query(call.id, "Already sent")
            return
        send(useful_chat_id, summary_text(article), disable_web_page_preview=True)
        store.mark_forwarded(conn, article_id)
        bot.answer_callback_query(call.id, "Sent to useful Germany")
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except telebot.apihelper.ApiTelegramException:
            pass
    finally:
        conn.close()


if __name__ == "__main__":
    bot.polling(non_stop=True, allowed_updates=["callback_query"])
