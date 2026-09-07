"""SQLite store for enriched articles.

Serves two purposes:
  * de-duplication - the set of links we have already sent (replaces the old
    flat ``processed`` file);
  * a queryable history (category, location, keywords, date) that ``digest.py``
    aggregates into the daily topic digest.

The database file lives next to this module so the path is independent of the
working directory, mirroring ``enrich.py``.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).with_name("articles.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id               INTEGER PRIMARY KEY,
    link             TEXT NOT NULL UNIQUE,
    feed_url         TEXT NOT NULL,
    title            TEXT NOT NULL,
    published        TEXT NOT NULL,   -- ISO8601, local time (matches feed parsing)
    fetched_at       TEXT NOT NULL,   -- ISO8601, UTC
    category         TEXT,
    country          TEXT,
    region           TEXT,
    city             TEXT,
    summary_ru       TEXT,
    practical_impact INTEGER,         -- 0/1/NULL
    impact_reason    TEXT,
    scraped          INTEGER,         -- 0/1/NULL
    sent_chat        TEXT             -- Telegram chat id the message went to
);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published);
CREATE INDEX IF NOT EXISTS idx_articles_category  ON articles(category);

CREATE TABLE IF NOT EXISTS keywords (
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    keyword    TEXT NOT NULL,
    PRIMARY KEY (article_id, keyword)
);
CREATE INDEX IF NOT EXISTS idx_keywords_keyword ON keywords(keyword);
"""


def connect(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def seen_links(conn) -> set:
    return {row[0] for row in conn.execute("SELECT link FROM articles")}


def record(
    conn,
    *,
    link,
    feed_url,
    title,
    published,
    category=None,
    location=None,
    summary_ru=None,
    practical_impact=None,
    impact_reason=None,
    scraped=None,
    keywords=(),
    sent_chat=None,
):
    """Insert one article. No-op if the link is already stored."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO articles
               (link, feed_url, title, published, fetched_at, category,
                country, region, city, summary_ru, practical_impact,
                impact_reason, scraped, sent_chat)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            link,
            feed_url,
            title,
            published,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            category,
            getattr(location, "country", None),
            getattr(location, "region", None),
            getattr(location, "city", None),
            summary_ru,
            None if practical_impact is None else int(practical_impact),
            impact_reason,
            None if scraped is None else int(scraped),
            sent_chat,
        ),
    )
    if cur.rowcount and cur.lastrowid is not None:
        conn.executemany(
            "INSERT OR IGNORE INTO keywords (article_id, keyword) VALUES (?, ?)",
            [(cur.lastrowid, k) for k in keywords],
        )
    conn.commit()
