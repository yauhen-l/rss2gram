"""Enrich an RSS article with location, category, and a Russian summary via Claude.

Strategy: scrape the article body ourselves (Anthropic's web_fetch user agent is
blocked by most news sites), fall back to the text carried in the RSS entry, and
hand whatever we have to Claude.
"""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

import anthropic
import requests
import trafilatura
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).with_name(".env"))  # ANTHROPIC_API_KEY / TELEGRAM_*, cwd-independent

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
    impact_reason: str = Field(
        description="One short English sentence: what concrete thing, if anything, "
        "a resident of Germany would decide or do differently after reading this."
    )
    practical_impact: bool = Field(
        description=(
            "True ONLY if the article reports a nationwide change in Germany (or "
            "one reaching a large share of the country) that an average resident "
            "would plausibly change a real decision or action over - things like: "
            "money (prices, taxes, benefits, pensions, energy bills, wages), law "
            "and rules (new regulations, deadlines, permits, registration, visas), "
            "infrastructure and public services (nationwide transport, healthcare, "
            "schools, utilities, strikes), broad safety, or consumer and product "
            "warnings. "
            "False for: local and regional news affecting only one city, district "
            "or Bundesland (road works, local council decisions, single-town "
            "events, regional strikes); military/defence procurement and weapons "
            "tests; foreign politics and diplomacy; war coverage; party manoeuvring "
            "with nothing enacted; opinion and analysis; sport; culture; celebrity; "
            "isolated crime; general background pieces. When unsure, answer false."
        )
    )


IMPACT_KEY = "practical_impact"

SYSTEM = (
    "You analyse news articles. Determine the geographic location the article is "
    "about (country, region, city; use null for parts that do not apply), pick "
    "the single best category, write a summary of exactly 4 sentences in Russian "
    "based only on the text provided, then decide practical_impact per its rule "
    "below - default to false, and set it true only when you can name the concrete "
    "action or decision a resident would change.\n\n"
    "practical_impact is true ONLY for a nationwide change in Germany (or one "
    "reaching a large share of the country) that an average resident would "
    "plausibly change a real decision or action over (money, law and rules, "
    "public services, broad safety, consumer warnings). It is false for local "
    "and regional news limited to one city, district or Bundesland; military and "
    "weapons news; foreign politics; war coverage; un-enacted political "
    "manoeuvring; opinion; sport; culture; celebrity; isolated crime; and "
    "background pieces.\n\n"
    "Reply with ONLY a JSON object, no prose, no markdown fences, matching:\n"
    '{"location": {"country": str|null, "region": str|null, "city": str|null}, '
    '"category": one of ' + json.dumps(list(Category.__args__)) + ", "
    '"summary_ru": "<exactly 4 sentences in Russian>", '
    '"impact_reason": "<one short English sentence>", '
    '"practical_impact": true|false}'
)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_reply(text: str) -> ArticleInfo:
    return ArticleInfo.model_validate_json(_FENCE.sub("", text).strip())


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

    messages = [{
        "role": "user",
        "content": f"Title: {title}\nURL: {link}\n\nArticle text:\n{body}",
    }]

    last_err = None
    for _ in range(2):
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM,
            messages=messages,
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        try:
            return Result(info=_parse_reply(text), scraped=scraped)
        except Exception as ex:  # noqa: BLE001 - retry once with the model's own output
            last_err = ex
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user", "content": f"Invalid: {ex}. Reply with only the JSON object."},
            ]

    raise RuntimeError(f"Bad JSON from model: {last_err}")
