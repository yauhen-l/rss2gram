"""Turn a window of stored articles into a country-first briefing via Claude.

One call in, one structured briefing out:

    country  ->  sections (one story / tight sub-topic, ordered by importance)
                 each section carries a Russian summary synthesised from the
                 member articles' own stored summaries, plus the member ids.

The point is the per-section summary; the list of source links is secondary.

Falls back to a flat country/category grouping with no summary on any error.
"""

import json
import os
import re
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

_workspace_id = os.getenv("ANTHROPIC_WORKSPACE_ID")
_headers = {"anthropic-workspace-id": _workspace_id} if _workspace_id else None
client = anthropic.Anthropic(default_headers=_headers)

# haiku-4-5 is cheaper but noticeably weaker at the synthesis; overridable.
MODEL = os.getenv("DIGEST_MODEL", "claude-sonnet-5")

MAX_ARTICLES = 250      # above this, skip the call and just group
SUMMARY_CHARS = 500     # trim each article's stored summary in the prompt
OTHER = "Разное"

SYSTEM = (
    "You build a daily news briefing in Russian for a reader living in Germany. "
    "Input: a JSON list of articles {id, title, country, region, city, "
    "category, keywords, summary}. 'summary' is a short Russian summary already "
    "written for that article.\n\n"
    "Produce a briefing GROUPED FIRST BY COUNTRY, then split into sub-topics:\n"
    "- Top-level group = the country the news is about, Russian name. Use "
    '"Международное" for cross-border / diplomacy items with no single country, '
    'and "' + OTHER + '" for technology, culture and off-topic feed items.\n'
    "- Within a country, create sections. A section is ONE story or a tight "
    "sub-topic - e.g. every article about the Sachsen-Anhalt election is a "
    "single section. Merge aggressively: the reader wants few sections.\n"
    "- Order countries, and sections within a country, by importance to a "
    "resident of Germany - Germany-wide practical impact first.\n"
    "- 'summary': 2-5 Russian sentences covering the important developments "
    "ACROSS ALL the section's articles - what happened, key numbers, reactions, "
    "what it means. A briefing paragraph, not a list. Use only the provided "
    "titles and summaries.\n"
    "- 'title': short Russian section title, 3-7 words.\n"
    "- 'ids': every article id you put in that section. Each input id must "
    "appear in exactly one section.\n\n"
    "Inside any string value use the guillemets « » for quotation, never the "
    "straight double quote \" - it must appear only as a JSON string delimiter. "
    "Put no literal newlines inside string values.\n\n"
    "Reply with ONLY JSON, no prose, no fences:\n"
    '{"groups": [{"country": str, "sections": [{"title": str, "summary": str, '
    '"ids": [int, ...]}, ...]}, ...]}'
)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _payload(rows):
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "country": r.get("country"),
            "region": r.get("region"),
            "city": r.get("city"),
            "category": r.get("category"),
            "keywords": r.get("keywords", []),
            "summary": (r.get("summary_ru") or "")[:SUMMARY_CHARS],
        }
        for r in rows
    ]


def _fallback(rows):
    groups = {}
    for r in rows:
        c = r.get("country") or OTHER
        groups.setdefault(c, {}).setdefault(r.get("category") or "Other", []).append(r["id"])
    return {
        "fallback": True,
        "groups": [
            {
                "country": c,
                "sections": [
                    {"title": cat, "summary": "", "ids": ids}
                    for cat, ids in cats.items()
                ],
            }
            for c, cats in groups.items()
        ],
    }


def build(rows):
    if not rows or len(rows) > MAX_ARTICLES:
        return _fallback(rows)

    messages = [
        {"role": "user", "content": json.dumps(_payload(rows), ensure_ascii=False)}
    ]
    try:
        data = None
        last_err = None
        for _ in range(2):
            resp = client.messages.create(
                model=MODEL,
                max_tokens=8192,
                system=SYSTEM,
                messages=messages,
            )
            text = "".join(b.text for b in resp.content if b.type == "text")
            try:
                data = json.loads(_FENCE.sub("", text).strip())
                break
            except json.JSONDecodeError as ex:
                last_err = ex
                messages += [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": f"Invalid JSON: {ex}. Reply with only the JSON object."},
                ]
        if data is None:
            raise RuntimeError(f"bad JSON from model: {last_err}")

        want = {r["id"] for r in rows}
        placed = {i for g in data["groups"] for s in g["sections"] for i in s["ids"]}
        missing = sorted(want - placed)
        if missing:
            data["groups"].append(
                {
                    "country": OTHER,
                    "sections": [{"title": "Прочее", "summary": "", "ids": missing}],
                }
            )
        return data
    except Exception as ex:  # noqa: BLE001 - briefing is best-effort
        print("briefing failed:", ex)
        return _fallback(rows)
