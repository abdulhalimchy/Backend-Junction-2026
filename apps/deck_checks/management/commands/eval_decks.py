# ruff: noqa: E501
"""Run deck checks on labelled decks and score them: `python manage.py eval_decks [--runs 2] [--no-search]`.

Calls the live LLM and Cala APIs. See evals/README.md for the gold-file format.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.deck_checks.evaluation import load_gold, score
from apps.deck_checks.pipeline import check_pdf

DEFAULT_DIR = Path(settings.BASE_DIR) / "evals" / "decks"
TOTAL_KEYS = (
    "items",
    "ideal",
    "ok",
    "wrong",
    "missed",
    "false_differs",
    "false_confirmed",
    "claims",
    "resolved",
)


class Command(BaseCommand):
    help = "Score the deck-check pipeline against hand-labelled decks."

    def add_arguments(self, parser):
        parser.add_argument("gold", nargs="*", help="Gold JSON files (default: every file in evals/decks).")
        parser.add_argument("--runs", type=int, default=1, help="Runs per deck, to measure stability.")
        parser.add_argument("--no-search", action="store_true", help="Skip the per-claim web search pass.")
        parser.add_argument(
            "--verbose", "-V", action="store_true", help="Show every item, not only problems."
        )

    def handle(self, *args, **opts):
        paths = [Path(p) for p in opts["gold"]] or sorted(DEFAULT_DIR.glob("*.json"))
        if not paths:
            raise CommandError(f"No gold files found in {DEFAULT_DIR}")
        if opts["no_search"]:
            settings.DECK_CHECK_WEB_SEARCH = False

        report, grand = [], Counter()
        for path in paths:
            gold = load_gold(path)
            if not gold.pdf.exists():
                self.stderr.write(f"Skipping {gold.name}: {gold.pdf} not found")
                continue
            pdf = gold.pdf.read_bytes()
            outcomes: dict[int, list[str]] = {}
            for run in range(opts["runs"]):
                self.stdout.write(self.style.MIGRATE_HEADING(f"\n{gold.name} — run {run + 1}/{opts['runs']}"))
                rows = check_pdf(pdf, company_name=gold.company_name).rows
                scored = score(gold, rows)
                self._print(scored, opts["verbose"])
                grand.update({k: scored["totals"][k] for k in TOTAL_KEYS})
                for n, r in enumerate(scored["results"]):
                    outcomes.setdefault(n, []).append(",".join(r["got"]) or "-")
                report.append(
                    {
                        "deck": gold.name,
                        "run": run + 1,
                        "totals": scored["totals"],
                        "items": [
                            {"slide": r["item"].slide, "keys": r["item"].all, "want": r["item"].want,
                             "got": r["got"], "outcome": r["outcome"], "rows": r["rows"]}
                            for r in scored["results"]
                        ],
                        "extra": scored["extra"],
                    }
                )  # fmt: skip
            if opts["runs"] > 1:
                stable = sum(1 for v in outcomes.values() if len(set(v)) == 1)
                self.stdout.write(f"Stable across runs: {stable}/{len(outcomes)} items")
                grand["stable"] += stable
                grand["stable_of"] += len(outcomes)

        self._summary(grand)
        out = Path(settings.BASE_DIR) / "evals" / "runs" / f"{datetime.now():%Y%m%d-%H%M%S}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        self.stdout.write(f"\nFull report: {out}")

    def _print(self, scored: dict, verbose: bool) -> None:
        marks = {"ideal": "✓", "ok": "~", "wrong": "✗", "missed": "?"}
        for r in scored["results"]:
            if not verbose and r["outcome"] in ("ideal", "ok"):
                continue
            item = r["item"]
            line = f"  {marks[r['outcome']]} s{item.slide} {'+'.join(item.all)}: got {r['got'] or 'no claim'}, want {item.want}"
            style = self.style.ERROR if r["outcome"] == "wrong" else self.style.WARNING
            self.stdout.write(style(line) if r["outcome"] in ("wrong", "missed") else line)
        for r in scored["extra"]:
            if r["status"] == "differs" or verbose:
                self.stdout.write(f"  + unlabelled s{r['slide']} [{r['status']}] {r['claim'][:90]}")
        t = scored["totals"]
        self.stdout.write(
            f"  items {t['items']}: {t['ideal']} ideal, {t['ok']} acceptable, {t['wrong']} wrong, {t['missed']} missed"
            f" | claims {t['claims']}, resolved {t['resolved']} | false differs {t['false_differs']}"
        )

    def _summary(self, g: Counter) -> None:
        if not g["items"]:
            return
        self.stdout.write(self.style.MIGRATE_HEADING("\nSummary"))
        pct = lambda a, b: f"{100 * a / b:.0f}%" if b else "-"  # noqa: E731
        self.stdout.write(
            f"  Error rate:     {pct(g['wrong'], g['items'])} ({g['wrong']}/{g['items']} items)"
        )
        self.stdout.write(f"  False differs:  {g['false_differs']}   False confirmed: {g['false_confirmed']}")
        self.stdout.write(f"  Ideal verdicts: {pct(g['ideal'], g['items'])}")
        self.stdout.write(f"  Missed items:   {g['missed']}")
        self.stdout.write(f"  Claims resolved (confirmed or differs): {pct(g['resolved'], g['claims'])}")
        if g["stable_of"]:
            self.stdout.write(f"  Stable across runs: {pct(g['stable'], g['stable_of'])}")
