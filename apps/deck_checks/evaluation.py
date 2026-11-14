"""Scores deck-check results against hand-labelled gold files (see evals/README.md).

Claim extraction words claims differently from run to run, so gold items are matched by slide and keywords,
not exact text. Each gold item lists the verdicts that are acceptable (`accept`) and the one we want (`want`):
a verdict outside `accept` is an error; one equal to `want` counts towards coverage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

STATUSES = ("confirmed", "differs", "cant_verify")


@dataclass
class GoldItem:
    slide: int
    all: list[str]
    accept: list[str]
    want: str
    none: list[str] = field(default_factory=list)
    any: list[str] = field(default_factory=list)
    note: str = ""

    def matches(self, row: dict) -> bool:
        text = row["claim"].lower()
        return (
            abs(row["slide"] - self.slide) <= 1
            and all(k.lower() in text for k in self.all)
            and not any(k.lower() in text for k in self.none)
            and (not self.any or any(k.lower() in text for k in self.any))
        )


@dataclass
class Gold:
    name: str
    pdf: Path
    company_name: str
    items: list[GoldItem]


def load_gold(path: Path) -> Gold:
    data = json.loads(path.read_text())
    items = []
    for raw in data["items"]:
        item = GoldItem(**raw)
        bad = {*item.accept, item.want} - set(STATUSES)
        if bad or item.want not in item.accept:
            raise ValueError(f"{path.name}: bad gold item {raw} ({bad or 'want not in accept'})")
        items.append(item)
    return Gold(path.stem, (path.parent / data["pdf"]).resolve(), data.get("company_name", ""), items)


def score(gold: Gold, rows: list[dict]) -> dict:
    """Per-item outcome plus totals for one run of one deck."""
    used: set[int] = set()
    results = []
    for item in gold.items:
        hits = [i for i, r in enumerate(rows) if item.matches(r)]
        used.update(hits)
        got = [str(rows[i]["status"]) for i in hits]
        if not got:
            outcome = "missed"
        elif any(s not in item.accept for s in got):
            outcome = "wrong"
        else:
            outcome = "ideal" if all(s == item.want for s in got) else "ok"
        results.append({"item": item, "got": got, "outcome": outcome, "rows": [rows[i] for i in hits]})

    extra = [r for i, r in enumerate(rows) if i not in used]
    count = lambda o: sum(1 for r in results if r["outcome"] == o)  # noqa: E731
    wrong = [r for r in results if r["outcome"] == "wrong"]
    resolved = sum(1 for r in rows if r["status"] != "cant_verify")
    return {
        "results": results,
        "extra": extra,
        "totals": {
            "items": len(results),
            "ideal": count("ideal"),
            "ok": count("ok"),
            "wrong": count("wrong"),
            "missed": count("missed"),
            # The costly mistake: telling an investor a true claim differs from the record.
            "false_differs": sum(
                1 for r in wrong if "differs" in r["got"] and "differs" not in r["item"].accept
            ),
            "false_confirmed": sum(
                1 for r in wrong if "confirmed" in r["got"] and "confirmed" not in r["item"].accept
            ),
            "claims": len(rows),
            "resolved": resolved,
            "extra_differs": sum(1 for r in extra if r["status"] == "differs"),
        },
    }
