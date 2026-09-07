"""Daily topic digest.

Aggregates the articles ``rss2gram.py`` stored in SQLite over a time window
(default: the last 24 hours), groups them into story clusters with Claude (see
``cluster.py``), lays the clusters out under their category, and sends a compact
summary to Telegram. Intended to be run once a day from cron.

Usage:
    python digest.py                 # last 24h
    python digest.py --hours 48      # last 48h
    python digest.py --since 2026-09-06        # since local midnight of that day
    python digest.py --no-cluster    # skip the Claude call, one line per article
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
from cluster import cluster as cluster_articles

load_dotenv(Path(__file__).with_name(".env"))

TG_LIMIT = 4000  # a little under Telegram's 4096 hard cap
TOP_KEYWORDS = 15


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--hours", type=float, default=24.0)
    g.add_argument("--since", metavar="YYYY-MM-DD")
    p.add_argument("--no-cluster", action="store_true")
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
    cols = ["id", "link", "title", "category", "country", "region", "city"]
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

    kw_by_article = {}
    kw_counts = Counter()
    for aid, kw in conn.execute(
        """SELECT kw.article_id, kw.keyword
               FROM keywords kw
               JOIN articles a ON a.id = kw.article_id
              WHERE a.published >= ?""",
        (since_iso,),
    ):
        kw_by_article.setdefault(aid, []).append(kw)
        kw_counts[kw] += 1
    for r in rows:
        r["keywords"] = kw_by_article.get(r["id"], [])

    return rows, kw_counts


def _bullet(r) -> str:
    loc = place(r)
    loc = " — _{}_".format(loc) if loc else ""
    return "• [{}]({}){}".format(r["title"], r["link"], loc)


def render(rows, kw_counts, clusters, since: datetime):
    if not rows:
        return ["\U0001F4F0 No articles since {:%Y-%m-%d %H:%M}.".format(since)]

    by_id = {r["id"]: r for r in rows}

    header = "\U0001F4F0 *Daily digest* — {n} articles / {c} topics since {t:%Y-%m-%d %H:%M}".format(
        n=len(rows), c=len(clusters), t=since
    )

    # attach each cluster to the category most of its articles carry
    cat_clusters = {}
    for c in clusters:
        arts = [by_id[i] for i in c["ids"] if i in by_id]
        if not arts:
            continue
        cat = Counter(a["category"] or "Other" for a in arts).most_common(1)[0][0]
        cat_clusters.setdefault(cat, []).append((c["label"], arts))

    blocks = [header]
    for cat in sorted(cat_clusters):
        items = cat_clusters[cat]
        n = sum(len(a) for _, a in items)
        # multi-article stories first (largest first), then singletons
        items.sort(key=lambda it: -len(it[1]))
        lines = ["\n*{}* ({})".format(cat, n)]
        for label, arts in items:
            if len(arts) == 1:
                lines.append(_bullet(arts[0]))
            else:
                lines.append("▸ *{}* ({})".format(label, len(arts)))
                lines += ["  " + _bullet(a) for a in arts]
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

    if args.no_cluster:
        clusters = [{"label": r["title"], "ids": [r["id"]]} for r in rows]
    else:
        clusters = cluster_articles(rows)

    msgs = render(rows, kw_counts, clusters, since)

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
