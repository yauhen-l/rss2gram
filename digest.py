"""Daily topic digest.

Pulls the articles ``rss2gram.py`` stored in SQLite over a time window (default:
last 24h) and sends a written, newspaper-style Russian digest to Telegram:
``briefing.py`` makes one Claude call that merges same-story articles and groups
them by country, then topic, most important first.

Usage:
    python digest.py                 # last 24h
    python digest.py --hours 48
    python digest.py --since 2026-09-06        # since local midnight of that day
    python digest.py --no-llm        # skip Claude, flat country/category list
    python digest.py --dry-run       # print, do not send

Chat id: TELEGRAM_DIGEST_CHAT_ID, falling back to TELEGRAM_CHAT_ID.
"""

import argparse
import os
import re
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
    return FLAGS.get(country.strip(), "\U0001F30D")


def build_rows(conn, since: datetime):
    since_iso = since.isoformat()
    cols = ["id", "link", "title", "category", "country", "region", "city", "summary_ru"]
    return [
        dict(zip(cols, r))
        for r in conn.execute(
            f"""SELECT {", ".join(cols)}
                   FROM articles
                  WHERE published >= ?
               ORDER BY published DESC""",
            (since_iso,),
        )
    ]


def md_to_tg(md: str) -> str:
    """Legacy-Markdown for Telegram: no ##/### headings, no ** bold."""
    out = []
    for line in md.splitlines():
        m = re.match(r"^(#{1,2})\s+(.*)$", line)
        if m:
            name = m.group(2).strip().strip("*")
            out.append("\n{} *{}*".format(flag(name), name.upper()))
            continue
        m = re.match(r"^#{3,}\s+(.*)$", line)
        if m:
            out.append("*{}*".format(m.group(1).strip().strip("*")))
            continue
        out.append(line.replace("**", "*"))
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_messages(text: str, first_prefix: str = "") -> list:
    chunks, cur = [], first_prefix
    for para in text.split("\n\n"):
        add = ("\n\n" if cur else "") + para
        if cur and len(cur) + len(add) > TG_LIMIT:
            chunks.append(cur)
            cur = para
        else:
            cur += add
        while len(cur) > TG_LIMIT:  # a single oversized paragraph
            chunks.append(cur[:TG_LIMIT])
            cur = cur[TG_LIMIT:]
    if cur:
        chunks.append(cur)
    return chunks


def render_fallback(rows, data) -> str:
    by_id = {r["id"]: r for r in rows}
    lines = []
    for g in data["groups"]:
        lines.append("\n{} *{}*".format(flag(g["country"]), g["country"].upper()))
        for sec in g["sections"]:
            arts = [by_id[i] for i in sec["ids"] if i in by_id]
            refs = " ".join("[{}]({})".format(n, a["link"]) for n, a in enumerate(arts, 1))
            lines.append("*{}* ({})\n{}".format(sec["title"], len(arts), refs))
    return "\n".join(lines).strip()


def render(rows, data, since: datetime) -> list:
    if not rows:
        return ["\U0001F4F0 Нет статей с {:%d.%m %H:%M}.".format(since)]

    tag = " · без ИИ-сводки" if data.get("fallback") else ""
    header = "\U0001F4F0 *Дайджест* — {} статей · {:%d.%m %H:%M}{}".format(
        len(rows), since, tag
    )
    body = render_fallback(rows, data) if data.get("fallback") else md_to_tg(data["markdown"])
    return split_messages(body, header)


def main():
    args = parse_args()
    since = cutoff(args)

    conn = store.connect()
    rows = build_rows(conn, since)
    conn.close()

    data = briefing._fallback(rows) if args.no_llm else briefing.build(rows)
    if data.get("stop_reason") not in (None, "end_turn"):
        print("briefing stop_reason:", data.get("stop_reason"))
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
