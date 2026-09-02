"""Manual check: enrich a single article and print the result.

Usage:
    python try_one.py                      # first entry of the default feed
    python try_one.py <feed_url>           # first entry of that feed
    python try_one.py <feed_url> <index>   # nth entry
"""

import sys

import feedparser

from enrich import enrich

feed_url = sys.argv[1] if len(sys.argv) > 1 else "http://rss.dw.de/xml/rss-ru-all"
index = int(sys.argv[2]) if len(sys.argv) > 2 else 0

feed = feedparser.parse(feed_url)
entry = feed.entries[index]

print("TITLE:", entry.title)
print("LINK :", entry.link)
print("FEED TEXT LEN:", len(getattr(entry, "summary", "") or ""))
print("-" * 60)

res = enrich(entry)
info = res.info

print("SCRAPED :", res.scraped)
print("COUNTRY :", info.location.country)
print("REGION  :", info.location.region)
print("CITY    :", info.location.city)
print("CATEGORY:", info.category)
print("FOR DE  :", info.relevant_for_germans)
print("SUMMARY :")
print(info.summary_ru)
