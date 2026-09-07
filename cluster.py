"""Group a day's articles into story clusters with Claude.

``category`` (a fixed enum) is stable but coarse; free-text keyword equality is
too brittle to merge "streik" / "bahnstreik" / "deutsche bahn". So we hand the
whole day's list (title + keywords + category) to Claude once and let it decide
which articles are the same underlying story.

On any failure the caller falls back to one cluster per article.
"""

import json
import re

from enrich import client, MODEL

MAX_ARTICLES = 200  # guard the prompt size; digests are far smaller in practice

SYSTEM = (
    "You group news articles into clusters. Each cluster is ONE underlying "
    "story, event or tightly-related topic - articles that a reader would see "
    "as 'the same news'. Different angles or follow-ups on the same event "
    "belong together; merely sharing a broad theme (both about 'economy') does "
    "not. Most clusters will be a single article; that is fine.\n\n"
    "Input is a JSON list of {id, title, category, keywords}. Every id must "
    "appear in exactly one cluster. Give each cluster a short label (3-6 words, "
    "in the language of that cluster's titles) naming the story.\n\n"
    "Reply with ONLY a JSON object, no prose, no markdown fences:\n"
    '{"clusters": [{"label": "<short label>", "ids": [<int>, ...]}, ...]}'
)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _payload(rows):
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "category": r["category"],
            "keywords": r.get("keywords", []),
        }
        for r in rows
    ]


def cluster(rows):
    """rows: dicts with id/title/category/keywords.

    Returns list of {"label": str, "ids": [int, ...]}. Falls back to one
    singleton cluster per row on any error or if the model drops/loses ids.
    """
    singletons = [{"label": r["title"], "ids": [r["id"]]} for r in rows]
    if len(rows) < 2 or len(rows) > MAX_ARTICLES:
        return singletons

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM,
            messages=[{"role": "user", "content": json.dumps(_payload(rows), ensure_ascii=False)}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        clusters = json.loads(_FENCE.sub("", text).strip())["clusters"]

        want = {r["id"] for r in rows}
        got = [i for c in clusters for i in c["ids"]]
        if sorted(got) != sorted(want):  # dropped, duplicated or invented ids
            print("cluster: id set mismatch, falling back")
            return singletons

        return [
            {"label": str(c["label"]).strip() or "—", "ids": list(c["ids"])}
            for c in clusters
            if c["ids"]
        ]
    except Exception as ex:  # noqa: BLE001 - clustering is best-effort
        print("cluster failed:", ex)
        return singletons
