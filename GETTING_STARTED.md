# Getting Started — Content-Modeling Agent

Point this at **your** SAP Spartacus storefront and **your** Contentstack stack, and it will
model the storefront's content, seed it, publish it, and verify it round-trips — one command,
two human gates. No code changes needed.

## Prerequisites

- **Python 3.9+** (standard library only; `pip install -r requirements.txt` just adds optional
  `jsonschema` for tests).
- The **contentstack-spartacus connector** checked out (for its starter-pack content types).
  By default the agent looks for it as a sibling: `../contentstack-spartacus-feature/`.
- A **Contentstack stack** you can write to: API key + management token. A delivery token is
  optional (only the final delivery-read verify step uses it).
- Network access to your storefront's **OCC** endpoint.

## 1. Configure

```bash
cp .env.example .env      # then edit values
set -a; . ./.env; set +a  # load into your shell
```

Fill in `OCC_BASE_URL`, `CS_API_KEY`, `CS_MANAGEMENT_TOKEN`, `CS_ENVIRONMENT`, and — if your
stack isn't North-America — `CS_REGION_BASE` + `CS_CDA_BASE`. See `.env.example` for region URLs.

## 2. Dry run (safe — writes nothing)

```bash
python3 orchestrate.py --site <your-baseSite> --source live
```

This extracts your storefront, models it, and prints **both gate reports** — including any
**unknown component types** (Gate 1) and exactly what *would* be created (Gate 2) — without
touching your stack. List your baseSites first:

```bash
curl -sk "$OCC_BASE_URL/basesites" | python3 -m json.tool | grep uid
```

## 3. Full governed run

```bash
python3 orchestrate.py --site <your-baseSite> --source live \
    --approve-model --approve-publish --env "$CS_ENVIRONMENT"
```

Pipeline: `extract → nav-resolve → model → [Gate 1] → [Gate 2] → seed → publish → verify`.
It imports the connector's 14 content types, uploads assets, upserts entries, auto-creates the
environment, publishes, and runs the round-trip check. Idempotent — re-run any time to update.

## The two gates

| Gate | When it blocks | How to pass |
|---|---|---|
| **1 · model approval** | only if a component `typeCode` isn't in the profile yet | decide authored vs OCC-only in `registry/connector.registry.json`, then `--approve-model` |
| **2 · pre-publish** | always, before any write | review the summary, then `--approve-publish` |

Without the flags, the run is a complete dry run.

## Extending for a new component type (Gate 1)

When Gate 1 reports an unknown `typeCode`, add it to `registry/connector.registry.json`:
- **authored** (editors control it) → add to `component_map` with its connector content type +
  field mapping;
- **functional** (OCC renders it) → add to `occ_only_types.types`.

Re-run. The mapping now works for every future run and every teammate.

## Verify / inspect

```bash
# what landed in the stack
python3 skills/verify/verify_connector.py --inventory /tmp/orchestrate-<site>/inventory.json \
    --plan /tmp/orchestrate-<site>/connector-plan.json

# read a page back exactly as the connector will (needs CS_DELIVERY_TOKEN)
CS_ENVIRONMENT=$CS_ENVIRONMENT python3 skills/verify/cda_read.py --content-type landing_page --url /
```

## Wire the storefront

In your Spartacus app's Contentstack feature config, set `apiKey`, `deliveryToken`,
`environment`, `cmsPageContentType: 'landing_page'`, `localeMapping: { en: 'en-us' }`, and
`globalSlots: { contentType: 'global_slots' }`. Product/category pages use shared slugs
`/product` and `/category` — match them in the connector's `pageTypeMapping`.

## Troubleshooting

- **Blank pages** → almost always a `localeMapping` mismatch: your storefront language must map
  to a locale that exists in your stack (default content is `en-us`).
- **`--approve-publish` errors on missing creds** → `CS_API_KEY` / `CS_MANAGEMENT_TOKEN` not in
  the shell. Re-source `.env`.
- **Wrong region** → set both `CS_REGION_BASE` and `CS_CDA_BASE`.
- **Delivery-read verify skipped** → no `CS_DELIVERY_TOKEN`; harmless, round-trip still runs.
