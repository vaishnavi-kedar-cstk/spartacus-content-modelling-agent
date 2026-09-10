# content-model

**Phase 3a skill · the translate step.**

Reads a normalized inventory (from `storefront-extract`) plus the mapping registry and
emits a **stack-agnostic modeling plan**: Contentstack content-type schemas + an entry
plan. It calls no Contentstack API.

---

## When to use

- After `storefront-extract` produces an inventory, to turn it into schemas + entries.
- Re-run any time the inventory or the registry changes; output is deterministic.

## Inputs

| Arg | Notes |
|---|---|
| `--inventory` | inventory JSON from `storefront-extract` |
| `--registry` | `registry/registry.json` |
| `--out` | output directory |

```bash
python3 model.py --inventory ../../fixtures/electronics-spa.inventory.json \
  --registry ../../registry/registry.json --out ./out
```

## Outputs

- **`content-types.json`** — CMA-ready global-field + content-type definitions, **only for
  the active types this site actually uses** (registry `_`-metadata stripped). A site with
  no merchandising carousel doesn't get that type.
- **`entry-plan.json`** — everything to upsert:
  - `entries.components` — one per unique component `uid` (a shared component = one entry),
    fields mapped from OCC, `_localized` per non-primary language.
  - `entries.slots` — one per `slotId`, `components` = ordered references.
  - `entries.pages` — one per page, `slots` = ordered {position, slot reference}.
  - `assets` — one per media code, with the download URL.
- **`model-report.json`** — counts, `cs_types_used`, and `unknown_type_codes`.

## How references work (stack-agnostic)

Entries don't yet have Contentstack uids, so every reference is expressed **by upsert
key** — `{"_ref": true, "content_type": "...", "by": "slot_id", "value": "..."}`. `cs-seed`
resolves these to real entry uids at create time. Assets are `{"_asset": true, "code": "..."}`.

## Gate 1 — unknown types

Any `typeCode` not in the registry is routed to `passthrough_component` (never blocks) and
listed under `unknown_type_codes` in the report. That list is the agent's cue to pause for
human approval and extend the registry — after which the type gets a proper mapping and the
plan is re-generated.

## Field mapping notes

- `banner` unifies simple + responsive; `type_code` drives which media shape the connector
  emits. Media become `_asset` refs.
- `rich_text.content` carries the source HTML as `{"_rte_from_html": "..."}`; cs-seed
  converts HTML → RTE JSON on write.
- `product_carousel.product_codes` is split from the space-separated OCC string into an array.
- `passthrough_component` preserves `flex_type` + a verbatim `config{}` of all non-base
  fields — lossless.
