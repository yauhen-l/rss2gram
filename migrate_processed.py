"""One-shot: import the legacy flat ``processed`` file into the SQLite store.

Each line of ``processed`` is a link that was already sent. We insert a stub
row per link so de-duplication keeps working after the switch to SQLite. The
stub carries no metadata and a 1970 ``published`` timestamp, so it never shows
up in a digest window.

Usage:
    python migrate_processed.py [path-to-processed]
"""

import sys
from pathlib import Path

import store

EPOCH = "1970-01-01T00:00:00"


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("processed")
    if not path.exists():
        sys.exit("not found: {}".format(path))

    links = [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]

    conn = store.connect()
    before = len(store.seen_links(conn))
    for link in links:
        store.record(
            conn,
            link=link,
            feed_url="legacy",
            title=link,
            published=EPOCH,
        )
    after = len(store.seen_links(conn))
    conn.close()

    print("{} lines, {} new rows ({} -> {})".format(
        len(links), after - before, before, after))


if __name__ == "__main__":
    main()
