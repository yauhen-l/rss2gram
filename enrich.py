"""Enrich an RSS article with location, category, and a Russian summary via Claude.

Strategy: scrape the article body ourselves (Anthropic's web_fetch user agent is
blocked by most news sites), fall back to the text carried in the RSS entry, and
hand whatever we have to Claude.
"""

import os
from dataclasses import dataclass
from typing import Literal, Optional

import anthropic
import requests
import trafilatura
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()  # pull ANTHROPIC_API_KEY / TELEGRAM_* from a .env file if present

# An identity-linked API key must also name the workspace it acts in.
_workspace_id = os.getenv("ANTHROPIC_WORKSPACE_ID")
_headers = {"anthropic-workspace-id": _workspace_id} if _workspace_id else None

client = anthropic.Anthropic(default_headers=_headers)  # reads ANTHROPIC_API_KEY

MODEL = "claude-haiku-4-5"  # "claude-opus-5" for higher quality at ~2.5x input cost

MAX_BODY_CHARS = 12000  # cap what we send to the model

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

Category = Literal[
    "Economy",
    "Social",
    "Inner Politics",
    "Outer Politics",
    "Sport",
    "Culture",
    "Science and Technology",
    "Incidents",
    "Other",
]


class Location(BaseModel):
    country: Optional[str] = None
    region: Optional[str] = None
    city: Optional[str] = None


class ArticleInfo(BaseModel):
    location: Location
    category: Category
    summary_ru: str = Field(description="Exactly 4 sentences, in Russian.")
    relevant_for_germans: bool = Field(
        description=(
            "True only if this reports a real social, economic, or political "
            "change that meaningfully affects the everyday life of an average "
            "person living in Germany. False for foreign news with no German "
            "impact, sport, celebrity, local curiosities, and routine coverage."
        )
    )


SYSTEM = (
    "You analyse news articles. Determine the geographic location the article is "
    "about (country, region, city; use null for parts that do not apply), pick "
    "the single best category, write a summary of exactly 4 sentences in Russian "
    "based only on the text provided, and judge whether the article is relevant "
    "for an average person living in Germany."
)


@dataclass
class Result:
    info: ArticleInfo
    scraped: bool  # False -> only the short RSS teaser was available


def _feed_text(entry) -> str:
    """Whatever body text the RSS entry already carries."""
    if getattr(entry, "content", None):
        return entry.content[0].value
    return getattr(entry, "summary", "") or ""


def _scrape(url: str) -> str:
    """Best-effort full article text. Empty string on any failure."""
    try:
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=15)
        resp.raise_for_status()
        text = trafilatura.extract(resp.text, url=url) or ""
        return text.strip()
    except Exception as ex:  # noqa: BLE001 - scraping is best-effort
        print("Scrape failed for " + url, ex)
        return ""


def enrich(entry) -> Result:
    title = getattr(entry, "title", "")
    link = entry.link

    article = _scrape(link)
    scraped = bool(article)
    body = (article or _feed_text(entry))[:MAX_BODY_CHARS]

    response = client.messages.parse(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Title: {title}\nURL: {link}\n\nArticle text:\n{body}",
        }],
        output_format=ArticleInfo,
    )

    if response.parsed_output is None:
        raise RuntimeError(f"No parsed output (stop_reason={response.stop_reason})")

    return Result(info=response.parsed_output, scraped=scraped)
