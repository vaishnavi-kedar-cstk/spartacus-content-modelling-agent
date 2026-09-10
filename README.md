# Content-Modeling Agent — SAP Spartacus → Contentstack

Auto-models any SAP Spartacus storefront's content, seeds it into Contentstack in the shape
the **contentstack-spartacus connector** reads, publishes it, and verifies it round-trips.
Built to be reliable and shareable across SAP storefront teams.

## Quick start

New team? See **[GETTING_STARTED.md](GETTING_STARTED.md)**. In short:

```bash
cp .env.example .env && $EDITOR .env      # your OCC + stack settings
set -a; . ./.env; set +a

# DRY RUN (safe): extract → model + both gate reports, writes nothing
python3 orchestrate.py --site <your-baseSite> --source live

# FULL governed run
python3 orchestrate.py --site <your-baseSite> --source live \
    --approve-model --approve-publish --env "$CS_ENVIRONMENT"

# FULL run + i18n: also seed other locales (fully-localized OCC per language,
# incl. per-locale images). Locales are created if missing (fallback = master).
python3 orchestrate.py --site <your-baseSite> --source live \
    --approve-model --approve-publish --env "$CS_ENVIRONMENT" \
    --locales de=de-de,ja=ja-jp,zh=zh-cn
```

Backend, stack, region, and connector path are all configured via env (`.env.example`) —
no code changes to target a different storefront or stack.

`orchestrate.py` sequences the skills and enforces two **fail-closed human gates**:

```
extract → nav-resolve → model → [GATE 1] → [GATE 2] → seed → publish → verify → [localize per locale]
```

The optional **localize** stage (`--locales`) runs after verify: for each language it does a
full localized OCC extract → model → creates the locale → uploads per-locale images →
localizes + publishes every entry (pages, shell, components, nav) in that locale.

- **Gate 1 — model approval**: blocks only if an **unknown typeCode** appears (not yet in the
  profile). The human decides authored vs OCC-only; then `--approve-model`.
- **Gate 2 — pre-publish**: always blocks before any write. Shows exactly what will be created/
  published; then `--approve-publish`. Without it, the run is a complete dry run.

Idempotent (re-runs update in place) and resumable via the run dir (`/tmp/orchestrate-<site>/`,
with a `manifest.json` of stage statuses).

## Layout

```
orchestrate.py                     the agent (governed pipeline + gates)
registry/
  connector.registry.json          go-forward MAPPING PROFILE onto the connector's model
  connector.README.md              its spec
  registry.json (+ README)         v1 free-form model (superseded; kept for reference)
fixtures/                          golden inventories (electronics, powertools)
skills/
  storefront-extract/  extract.py · nav_resolve.py · inventory.schema.json · check_fixture.py
  content-model/       model_connector.py  (+ model.py for v1)
  cs-seed/             cma_seed_connector.py · publish.py  (+ cma_seed.py / seed.py for v1)
  verify/              verify_connector.py · cda_read.py  (+ verify_seed.py / roundtrip.py for v1)
```

## Model (connector profile, hybrid "islands")

OCC renders the base of every page; Contentstack overrides **only authored slots**. Functional
components + non-content pages stay in OCC. See `registry/connector.README.md`.

- **Pages** per template (`landing_page`, `content_page`, `product_page`, `category_page`),
  keyed by `url` slug.
- **Slots** = named reference fields on the page; **shell** = one `global_slots` entry.
- **Components** authored: banners, product carousel, paragraph, link, flex. **Nav**: nav_node
  tree + category/footer nav (via the nav-resolve pass). Functional types → OCC (not seeded).

Content-type schemas come from the connector's shipped starter pack; this repo only **maps**
OCC content onto them and seeds the data.

## Verification

- **Round-trip** (`verify_connector.py`): reverse-maps seeded entries → OCC shape, diffs vs the
  original inventory. Correctness by construction.
- **Delivery read** (`cda_read.py`): fetches a page by slug with references resolved using the
  **delivery token** — the connector's exact runtime fetch.

## Status

electronics-spa: seeded + published + verified (6 pages, ~40 authored components, full nav tree,
product/category pages) into a live stack, all round-trips passing. Pending: powertools seed,
locales (de/ja/zh), Live Preview, orphan cleanup. See project memory for the running log.
