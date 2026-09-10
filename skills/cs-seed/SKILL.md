# cs-seed

**Phase 3b skill · the write step.**

Reads the `content-model` output and produces an **ordered, idempotent seed plan**.
Default mode is **dry-run** (emits `seed-plan.json`, calls no API). Live seeding is guarded
behind credentials and intentionally not enabled in the stack-agnostic phase.

---

## When to use

- Dry-run: preview exactly what would be created/updated in a stack, in order. This is the
  input to the **Gate 2 (pre-publish)** review.
- Execute (later): apply the plan to a real stack once one is provisioned.

## Inputs

| Arg | Notes |
|---|---|
| `--model-dir` | dir containing `content-types.json` + `entry-plan.json` |
| `--out` | plan output (default `seed-plan.json` in the model dir) |
| `--execute` | apply live — requires `CS_API_KEY` + `CS_MANAGEMENT_TOKEN`; **not enabled yet** |

```bash
python3 seed.py --model-dir ../content-model/out            # dry-run
```

## Ordering (so references never dangle)

```
1. global fields
2. content types   — topologically sorted (references, incl. those nested in groups,
                      always precede their user: components → cms_content_slot → cms_page)
3. assets          — upserted by a sap_code tag
4. component entries  (+ localizations)
5. slot entries       (reference component entries)
6. page entries       (reference slot entries)
```

The plan self-checks for **dangling dependencies** — every `depends_on` must point at an
operation the plan actually emits. A clean plan reports `0`.

## Idempotency

Every operation is an **UPSERT** keyed by a stable unique field:

| Target | Key |
|---|---|
| content type / global field | `uid` |
| asset | `sap_code` tag |
| component entry | `sap_meta.sap_uid` |
| slot entry | `slot_id` |
| page entry | `page_label` |

Re-running the plan updates in place — never duplicates. "Update the seed data" is the same
command as "seed it."

## Live execution — `cma_seed.py`

`seed.py --execute` remains a guarded stub. Live seeding is done by **`cma_seed.py`**, the
Contentstack Management API executor. It applies a `content-model` output to a real stack,
idempotently, in dependency order.

```bash
export CS_API_KEY=...  CS_MANAGEMENT_TOKEN=...   # NA default; set CS_REGION_BASE for other regions
python3 cma_seed.py --model-dir <dir> --site electronics-spa --stages globals,content_types
python3 cma_seed.py --model-dir <dir> --site electronics-spa                 # full run
python3 cma_seed.py --model-dir <dir> --site electronics-spa --limit-entries 3   # smoke test
```

Stages: `globals, content_types, assets, entries` (comma list; default all). English
(master locale) only in this phase; localization is a clean follow-up once locales exist.

**Idempotency keys:** content types / global fields by `uid`; **assets by title** (= the
media code — tags can't exceed 50 chars, so they're not the key); entries by their unique
key field (`page_label` / `slot_id` / `sap_meta.sap_uid`). Re-running updates in place.

**CMA-safe schema adjustments** (in `normalize_content_type`, mirrored in registry v0.2.1):
- `rich_text.content` → HTML advanced RTE (stores OCC HTML directly).
- `passthrough_component.config` → multiline text holding JSON (CS has no generic json field).
- enum/select fields get `display_type: dropdown`.
- every content type gets a mandatory `title` field (CS requirement); entry `title` is set.

**Reference resolution:** a `key → entry-uid` map is built as entries are created, so
`_ref` placeholders resolve to `[{uid, _content_type_uid}]`; `_asset` placeholders resolve
via the asset cache. Order (components → slots → pages) guarantees targets exist first.

**Not yet wired:** publishing (the stack has no environment) and localization. Both are
additive follow-ups behind Gate 2.
