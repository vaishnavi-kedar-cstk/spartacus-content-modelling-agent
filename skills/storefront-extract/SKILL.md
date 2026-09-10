# storefront-extract

**Phase 2 skill · the read side of the content-modeling pipeline.**

Crawls one SAP Commerce `baseSite` over the OCC API and emits a normalized **content
inventory** JSON conforming to [`inventory.schema.json`](inventory.schema.json). That
inventory is the single contract every downstream skill (`content-model`, `cs-seed`,
`verify`) consumes.

---

## When to use

- Onboarding a new SAP Spartacus storefront (first inventory).
- Re-reading a storefront whose content changed (the input to "update the seed data").
- Regenerating the golden fixtures after an intentional extractor change.

## Inputs

| Arg | Default | Notes |
|---|---|---|
| `--site` | *(required)* | baseSite uid, e.g. `electronics-spa`. List via `GET /occ/v2/basesites`. |
| `--base` | demo OCC url | OCC v2 base, `https://HOST:PORT/occ/v2`. |
| `--langs` | site languages | Comma list, e.g. `en,de`. First is primary. |
| `--primary-only` | off | Skip i18n; crawl the primary language only (fast). |
| `--download-media DIR` | off | Also download media bytes to `DIR` (manifest is always captured). |
| `--out` | `inventory.json` | Output path. |

```bash
# fast structural pass
python3 extract.py --site electronics-spa --primary-only --out inv.json
# full pass with i18n + media bytes
python3 extract.py --site electronics-spa --download-media ./media --out inv.json
```

## Output — what the inventory contains

- `contentPages` — every content page found by **guided BFS** (seed labels + internal
  links), each with ordered slots → components, fully resolved by `fields=FULL`.
- `layoutSamples` — one representative Product and Category page (fetched by `code=`),
  so PDP/PLP slot structure is captured. Records themselves stay in commerce.
- `slots` — every slot with `position` + `shared` (shared = global header/footer/nav).
- `navigation` — resolved `navigationNode` trees (children + entry references). Captured
  now; the registry activates `navigation` content types in v0.2.
- `i18n` — per non-primary language, the localized text fields per component uid.
- `mediaManifest` — every media asset (`url` carries the `?context=` download token).
- `componentTypeInventory` — `typeCode -> {instances, fields}`, the diff surface for
  finding unknown types.

## Guarantees

- **Deterministic.** Stable key order, no wall-clock timestamps → byte-identical on
  re-run, so fixtures diff cleanly.
- **Lossless discovery of types.** Every `typeCode` present is inventoried, including
  ones the registry doesn't know yet (that's the Gate-1 signal for `content-model`).
- **Schema-valid.** Output validates against `inventory.schema.json`.

## Key facts encoded (validated live)

1. `/cms/pages` is **page-by-label, not a list** → BFS discovery, not a bulk dump.
2. `fields=FULL` inlines component fields, responsive media, **and** the nav node tree.
3. Product/Category **layouts** use `pageType=…&code=…` (not `pageLabelOrId`).
4. Media bytes come from `HOST + media.url` (the `?context=` token is mandatory).
5. Content is per content-catalog + version; we read the published **Online** version.

## Regression / self-check

The committed fixtures in `../../fixtures/` are the baseline. To check for drift:

```bash
python3 extract.py --site electronics-spa --out /tmp/new.json --quiet
python3 check_fixture.py electronics-spa   # compares counts + component-type set
```

`check_fixture.py` compares the structural counts and the component-type set against the
fixture and reports any NEW or MISSING types — a new type is the cue to run the Gate-1
extension flow in `content-model`, not a failure.

## Known limits / handoff to later phases

- **Navigation** is captured raw but not yet modeled as authored content (registry
  `navigation` is `_active:false`; activate in v0.2).
- **i18n** captures localized *text* fields; localized media/asset variants are not yet
  diffed.
- Layout sampling takes **one** Product and **one** Category page as representative; it
  does not enumerate every PDP/PLP (by design — those records live in commerce).
