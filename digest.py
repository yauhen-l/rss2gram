"""Daily topic digest.

Pulls the articles ``rss2gram.py`` stored in SQLite over a time window (default:
last 24h), asks Claude (see ``briefing.py``) to build a country-first briefing -
grouped by country, split into story sections, each with a synthesised Russian
summary - and sends it to Telegram. Meant to run once a day from cron.

Usage:
    python digest.py                 # last 24h
    python digest.py --hours 48
    python digest.py --since 2026-09-06        # since local midnight of that day
    python digest.py --no-llm        # skip Claude, flat country/category grouping
    python digest.py --dry-run       # print, do not send

Chat id: TELEGRAM_DIGEST_CHAT_ID, falling back to TELEGRAM_CHAT_ID.
"""

import argparse
import os
from datetime import datetime, timedelta
from pathlib import Path

import telebot
from dotenv import load_dotenv

import briefing
import store

load_dotenv(Path(__file__).with_name(".env"))

TG_LIMIT = 4000  # a little under Telegram's 4096 hard cap

FLAGS = {
    "Германия": "\U0001F1E9\U0001F1EA",
    "Украина": "\U0001F1FA\U0001F1E6",
    "Россия": "\U0001F1F7\U0001F1FA",
    "США": "\U0001F1FA\U0001F1F8",
    "Франция": "\U0001F1EB\U0001F1F7",
    "Великобритания": "\U0001F1EC\U0001F1E7",
    "Польша": "\U0001F1F5\U0001F1F1",
    "Сербия": "\U0001F1F7\U0001F1F8",
    "Гренландия": "\U0001F1EC\U0001F1F1",
    "Северная Корея": "\U0001F1F0\U0001F1F5",
    "Евросоюз": "\U0001F1EA\U0001F1FA",
    "Международное": "\U0001F30D",
    briefing.OTHER: "\U0001F5C2",
}


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--hours", type=float, default=24.0)
    g.add_argument("--since", metavar="YYYY-MM-DD")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def cutoff(args) -> datetime:
    if args.since:
        return datetime.fromisoformat(args.since)
    return datetime.now() - timedelta(hours=args.hours)


def flag(country: str) -> str:
    return FLAGS.get(country, "\U0001F30D")


def build_rows(conn, since: datetime):
    since_iso = since.isoformat()
    cols = ["id", "link", "title", "category", "country", "region", "city", "summary_ru"]
    rows = [
        dict(zip(cols, r))
        for r in conn.execute(
            f"""SELECT {", ".join(cols)}
                   FROM articles
                  WHERE published >= ?
               ORDER BY published DESC""",
            (since_iso,),
        )
    ]
    kw = {}
    for aid, keyword in conn.execute(
        """SELECT kw.article_id, kw.keyword
               FROM keywords kw JOIN articles a ON a.id = kw.article_id
              WHERE a.published >= ?""",
        (since_iso,),
    ):
        kw.setdefault(aid, []).append(keyword)
    for r in rows:
        r["keywords"] = kw.get(r["id"], [])
    return rows


def section_text(sec, by_id) -> str:
    arts = [by_id[i] for i in sec["ids"] if i in by_id]
    if not arts:
        return ""
    out = ["\n▸ *{}* ({})".format(sec["title"], len(arts))]
    if sec.get("summary"):
        out.append(sec["summary"])
    refs = " ".join("[{}]({})".format(n, a["link"]) for n, a in enumerate(arts, 1))
    out.append("_источники:_ " + refs)
    return "\n".join(out)


def render(rows, data, since: datetime):
    if not rows:
        return ["\U0001F4F0 Нет статей с {:%d.%m %H:%M}.".format(since)]

    by_id = {r["id"]: r for r in rows}
    n_sec = sum(len(g["sections"]) for g in data["groups"])
    tag = " (без ИИ-группировки)" if data.get("fallback") else ""
    header = "\U0001F4F0 *Дайджест* — {} статей, {} тем · {:%d.%m %H:%M}{}".format(
        len(rows), n_sec, since, tag
    )

    def country_head(c):
        return "{} *{}*".format(flag(c), c.upper())

    msgs, cur, cur_country = [], header, None
    for g in data["groups"]:
        country = g["country"]
        for sec in g["sections"]:
            body = section_text(sec, by_id)
            if not body:
                continue
            head = "" if country == cur_country else "\n\n" + country_head(country)
            piece = head + "\n" + body
            if len(cur) + len(piece) + 1 > TG_LIMIT:
                msgs.append(cur)
                cur = country_head(country) + " _(продолжение)_\n" + body
            else:
                cur += piece
            cur_country = country
    msgs.append(cur)
    return msgs


def main():
    args = parse_args()
    since = cutoff(args)

    conn = store.connect()
    rows = build_rows(conn, since)
    conn.close()

    data = briefing._fallback(rows) if args.no_llm else briefing.build(rows)
    msgs = render(rows, data, since)

    if args.dry_run:
        print(("\n\n" + "=" * 60 + "\n\n").join(msgs))
        return

    chat_id = os.getenv("TELEGRAM_DIGEST_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    bot = telebot.TeleBot(token, parse_mode="MARKDOWN")
    for m in msgs:
        bot.send_message(chat_id, m, disable_web_page_preview=True)


if __name__ == "__main__":
    main()
