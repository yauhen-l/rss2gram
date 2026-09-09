"""Turn a window of stored articles into a written daily digest via Claude.

One call in, Markdown out: a Russian "newspaper" for a reader living in Germany.
Articles about the same story (e.g. the Sachsen-Anhalt election covered by five
feeds) are merged into a single item with one aggregated paragraph - no
repetition. Grouped by country, then by topic, most important first.

``build`` returns {"markdown": str} on success, or a flat
{"fallback": True, "groups": [...]} structure (country -> category -> ids, no
prose) on any Claude/parse failure so the digest still goes out.
"""

import json
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

_workspace_id = os.getenv("ANTHROPIC_WORKSPACE_ID")
_headers = {"anthropic-workspace-id": _workspace_id} if _workspace_id else None
client = anthropic.Anthropic(default_headers=_headers)

# haiku-4-5 is cheaper; sonnet writes a markedly better digest. Overridable.
MODEL = os.getenv("DIGEST_MODEL", "claude-sonnet-5")

MAX_ARTICLES = 300
SUMMARY_CHARS = 400
OTHER = "Разное"

SYSTEM = (
    "Ты — редактор ежедневного новостного дайджеста на русском языке для "
    "читателя, живущего в Германии. На вход даётся JSON-список статей за сутки: "
    "{id, title, country, region, city, category, summary, url}. Поле summary — "
    "это уже готовое краткое изложение статьи.\n\n"
    "Сделай из этого дайджест, который читают как газету. Правила:\n"
    "1. Сначала сгруппируй по СТРАНЕ, о которой новость. Заголовок страны — "
    "'## <Страна>'. Для трансграничных/дипломатических сюжетов без одной страны "
    "используй '## Международное', для технологий, культуры и нерелевантного "
    "мусора — '## " + OTHER + "'.\n"
    "2. Внутри страны — темы. Одна тема = один сюжет. Все статьи об одном и том "
    "же событии (например, выборы в Саксонии-Анхальт) — это ОДНА тема, "
    "объединённая в один блок, без повторов. Объединяй агрессивно.\n"
    "3. Каждая тема: строка '### <краткий заголовок темы>', затем ОДИН абзац "
    "в 2–5 предложений — самое важное и интересное по всем статьям темы: что "
    "произошло, ключевые цифры, реакции, что это значит. Не список, а связный "
    "текст. Только факты из предоставленных summary и заголовков, ничего не "
    "выдумывай.\n"
    "4. Порядок стран и тем — по важности для жителя Германии: сначала то, что "
    "реально влияет на жизнь в стране, потом остальное.\n"
    "5. Мелкие однотипные местные новости (одна авария, локальное мероприятие) "
    "не раздувай — either объедини в один абзац 'Коротко', either опусти.\n"
    "6. В конце абзаца темы можешь дать ссылки в виде "
    "'([1](url) [2](url))', используя url из данных. Ссылки — второстепенны.\n\n"
    "Выдай ТОЛЬКО Markdown-текст дайджеста, без преамбулы и пояснений."
)


def _payload(rows):
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "country": r.get("country"),
            "region": r.get("region"),
            "city": r.get("city"),
            "category": r.get("category"),
            "summary": (r.get("summary_ru") or "")[:SUMMARY_CHARS],
            "url": r.get("link"),
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

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            messages=[
                {"role": "user", "content": json.dumps(_payload(rows), ensure_ascii=False)}
            ],
        )
        md = "".join(b.text for b in resp.content if b.type == "text").strip()
        if len(md) < 40:
            raise RuntimeError("suspiciously short digest")
        return {"markdown": md, "stop_reason": resp.stop_reason}
    except Exception as ex:  # noqa: BLE001 - best-effort; fall back to a flat list
        print("briefing failed:", ex)
        return _fallback(rows)
