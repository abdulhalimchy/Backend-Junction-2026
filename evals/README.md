# Deck-check evals

Hand-labelled decks that measure how well the deck check verifies claims. Run them before and after any
change to extraction, evidence, search or prompts.

```bash
python manage.py eval_decks              # every deck in evals/decks, one run each
python manage.py eval_decks --runs 3     # also measures how stable verdicts are between runs
python manage.py eval_decks --no-search  # record-only, to see what the web-search pass adds
```

It calls the live LLM and Cala APIs (a few minutes per deck with search on). A JSON report is written to
`evals/runs/` (git-ignored).

## What the numbers mean

- **Error rate / false differs**: verdicts outside what the label accepts. A false "differs" tells an investor
  a true claim is wrong, so it is the number to keep at zero.
- **Ideal verdicts**: how often we reached the best verdict (coverage).
- **Missed**: the label matched no extracted claim (extraction dropped or reworded it).
- **Claims resolved**: share of all extracted claims that got confirmed or differs.

## Adding a deck

1. Put the PDF in `evals/decks/` (PDFs are git-ignored; share them separately).
2. Add `<name>.json` next to it:

```json
{
  "pdf": "acme-seed.pdf",
  "company_name": "Acme",
  "items": [
    {"slide": 3, "all": ["jane doe", "cto"], "accept": ["confirmed"], "want": "confirmed"},
    {"slide": 5, "all": ["tam"], "accept": ["confirmed", "cant_verify"], "want": "cant_verify",
     "note": "Founders' own estimate"}
  ]
}
```

An item matches every extracted claim within one slide of `slide` whose text contains all of `all`, none of
`none`, and (if given) one of `any`. Label what is actually true, not what Cala happens to hold today:
`accept` lists every verdict that would not mislead an investor, `want` the best one.
