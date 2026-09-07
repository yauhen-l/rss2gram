"""Daily topic digest.

Aggregates the articles ``rss2gram.py`` stored in SQLite over a time window
(default: the last 24 hours), groups them by category, and sends a compact
summary to Telegram. Intended to be run once a day from cron.

Usage:
    python digest.py                 # last 24h
    python digest.py --hours 48      # last 48h
    python digest.py --since 2026-09-06        # since local midnight of that day
    python digest.py --dry-run       # print, do not send

Chat id: TELEGRAM_DIGEST_CHAT_ID, falling back to TELEGRAM_CHAT_ID.
"""

import argparse
import os
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import telebot
from dotenv import load_dotenv

import store

load_dotenv(Path(__file__).with_name(".env"))

TG_LIMIT = 4000  # a little under Telegram's 4096 hard cap
TOP_KEYWORDS = 15


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--hours", type=float, default=24.0)
    g.add_argument("--since", metavar="YYYY-MM-DD")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def cutoff(args) -> datetime:
    if args.since:
        return datetime.fromisoformat(args.since)
    return datetime.now() - timedelta(hours=args.hours)


def place(row) -> str:
    for key in ("city", "region", "country"):
        if row[key]:
            return row[key]
    return ""


def build(conn, since: datetime):
    since_iso = since.isoformat()
    rows = conn.execute(
        """SELECT id, link, title, category, country, region, city
               FROM articles
              WHERE published >= ?
           ORDER BY category, published DESC""",
        (since_iso,),
    ).fetchall()
    cols = ["id", "link", "title", "category", "country", "region", "city"]
    rows = [dict(zip(cols, r)) for r in rows]

    kw_counts = Counter(
        k
        for (k,) in conn.execute(
            """SELECT kw.keyword
                   FROM keywords kw
                   JOIN articles a ON a.id = kw.article_id
                  WHERE a.published >= ?""",
            (since_iso,),
        )
    )
    return rows, kw_counts


def render(rows, kw_counts, since: datetime):
    if not rows:
        return ["\U0001F4F0 No articles since {:%Y-%m-%d %H:%M}.".format(since)]

    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"] or "Other", []).append(r)

    header = "\U0001F4F0 *Daily digest* — {n} articles since {t:%Y-%m-%d %H:%M}".format(
        n=len(rows), t=since
    )

    blocks = [header]
    for cat in sorted(by_cat):
        lines = ["\n*{}* ({})".format(cat, len(by_cat[cat]))]
        for r in by_cat[cat]:
            loc = place(r)
            loc = " — _{}_".format(loc) if loc else ""
            lines.append("• [{}]({}){}".format(r["title"], r["link"], loc))
        blocks.append("\n".join(lines))

    if kw_counts:
        top = kw_counts.most_common(TOP_KEYWORDS)
        blocks.append(
            "\n\U0001F511 *Top keywords*\n"
            + ", ".join("{} ({})".format(k, c) if c > 1 else k for k, c in top)
        )

    # pack blocks into <=TG_LIMIT messages
    msgs, cur = [], ""
    for b in blocks:
        if cur and len(cur) + len(b) + 2 > TG_LIMIT:
            msgs.append(cur)
            cur = ""
        cur = b if not cur else cur + "\n\n" + b
    if cur:
        msgs.append(cur)
    return msgs


def main():
    args = parse_args()
    since = cutoff(args)

    conn = store.connect()
    rows, kw_counts = build(conn, since)
    conn.close()

    msgs = render(rows, kw_counts, since)

    if args.dry_run:
        print(("\n\n" + "-" * 60 + "\n\n").join(msgs))
        return

    chat_id = os.getenv("TELEGRAM_DIGEST_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    bot = telebot.TeleBot(token, parse_mode="MARKDOWN")
    for m in msgs:
        bot.send_message(chat_id, m, disable_web_page_preview=True)


if __name__ == "__main__":
    main()
