"""Long-running bot that handles the inline buttons on posts from rss2gram.py.

  * "Summary"               - enrich the article on demand and reply with the summary;
  * mention the bot in a message containing a link - it replies with a summary of that link;
  * "Send to useful Germany" - forward the article to TELEGRAM_USEFUL_GERMANY_CHAT_ID.

Run it as a service (rss2gram.py stays a cron job):
    python bot.py
"""

import os
import threading
from datetime import datetime
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


def _urls(message):
    """Distinct http(s) links in a message, in order (plain URLs and text links)."""
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    urls = []
    for e in entities:
        if e.type == "url":
            url = text[e.offset : e.offset + e.length]  # noqa: E203
        elif e.type == "text_link":
            url = e.url
        else:
            continue
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if url not in urls:
            urls.append(url)
    return urls


def _mentions_bot(message):
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    name = "@" + bot.get_me().username.lower()
    return any(
        e.type == "mention" and text[e.offset : e.offset + e.length].lower() == name  # noqa: E203
        for e in entities
    )


def _summarise_link(conn, url):
    """Article row for a user-posted link, enriching and storing it if new."""
    row = conn.execute("SELECT id FROM articles WHERE link = ?", (url,)).fetchone()
    article = load(conn, row[0]) if row else None
    if article and article["summary_ru"]:
        return article

    res = enrich(SimpleNamespace(title="", link=url, summary=""))
    if article:
        article_id = article["id"]
    else:
        article_id = store.record(
            conn,
            link=url,
            feed_url="manual",
            title=res.title or url,
            published=datetime.now().isoformat(timespec="seconds"),
        )
    store.save_enrichment(conn, article_id, res.info, res.scraped)
    return load(conn, article_id)


def _is_link_mention(m):
    return bool(_urls(m)) and _mentions_bot(m)


@bot.message_handler(func=_is_link_mention, content_types=["text", "photo"])
@bot.channel_post_handler(func=_is_link_mention, content_types=["text", "photo"])
def on_link_mention(message):
    for url in _urls(message):
        conn = store.connect()
        try:
            article = _summarise_link(conn, url)
            send(
                message.chat.id,
                summary_text(article),
                reply_to_message_id=message.message_id,
                reply_markup=summary_markup(article),
                disable_web_page_preview=True,
            )
        except Exception as ex:  # noqa: BLE001
            print("Summary failed for " + url, ex)
            bot.send_message(
                message.chat.id, "Failed to summarise: {}".format(ex), reply_to_message_id=message.message_id
            )
        finally:
            conn.close()


def _log_updates(messages):
    for m in messages:
        print("update: chat={} type={} text={!r}".format(m.chat.id, m.chat.type, (m.text or m.caption or "")[:80]), flush=True)


bot.set_update_listener(_log_updates)

if __name__ == "__main__":
    bot.polling(non_stop=True, allowed_updates=["callback_query", "message", "channel_post"])
