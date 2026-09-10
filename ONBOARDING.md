# Onboarding — SAP Spartacus → Contentstack Content-Modeling Agent

You're looking at an **agent** that models any SAP Spartacus storefront's content, seeds it
into Contentstack in the shape the contentstack-spartacus connector reads, publishes it, and
verifies it round-trips — with **one command and two human gates**. No code changes to point
it at a different storefront or stack.

## What it does

```
extract → nav-resolve → model → [GATE 1: model approval] → [GATE 2: pre-publish] → seed → publish → verify
```

- **extract** — crawls your storefront's OCC API into a normalized inventory.
- **model** — maps it onto the connector's content types (hybrid "islands": OCC renders the
  base, Contentstack overrides only the slots you author; functional components stay in OCC).
- **seed / publish** — idempotently upserts content types, assets, entries; publishes to an
  environment (auto-created if missing).
- **verify** — round-trips seeded entries back to the OCC shape and reads a page via the
  delivery API exactly as the connector does.

Proven end-to-end on **electronics** (B2C) and **powertools** (B2B) across multiple stacks.

## Run it (5 minutes)

Prereqs: Python 3.9+ (standard library only), a Contentstack stack (API key + management
token), and network access to your storefront's OCC endpoint.

```bash
cp .env.example .env          # set OCC_BASE_URL + your CS_* tokens + region
set -a; . ./.env; set +a

# 1) DRY RUN — extract + model + both gate reports, writes NOTHING
python3 orchestrate.py --site <your-baseSite> --source live

# 2) FULL governed run
python3 orchestrate.py --site <your-baseSite> --source live \
    --approve-model --approve-publish --env "$CS_ENVIRONMENT"
```

Find your baseSites: `curl -sk "$OCC_BASE_URL/basesites" | grep -o '"uid"[^,]*'`

## The two gates (why it's safe)

| Gate | Blocks when | Pass with |
|---|---|---|
| **1 · model approval** | a component type isn't mapped yet | review, map it in `registry/connector.registry.json`, `--approve-model` |
| **2 · pre-publish** | always, before any write | review the summary, `--approve-publish` |

No flags = a complete dry run. Nothing is written to your stack until you approve.

## Ground rules

- **Use your own stack tokens** in your own `.env` — never share or commit credentials
  (`.env` is git-ignored; only `.env.example` is tracked).
- Each run is **idempotent** — safe to re-run to update.

## Where things are

- `orchestrate.py` — the agent (start here).
- `GETTING_STARTED.md` — the full guide (config, gates, extending, troubleshooting).
- `registry/connector.registry.json` — the OCC→connector mapping profile (extend here for new
  component types).
- `skills/` — the individual steps; `fixtures/` — demo baselines; `vendor/starter-pack/` — the
  connector's content types (bundled, self-contained).

## Ask Claude Code

Good first prompts once you've opened this in Claude Code:
- "Dry-run the agent against my baseSite `<X>` and show me the two gate reports."
- "Gate 1 flagged an unknown type `<Y>` — help me decide authored vs OCC-only and map it."
- "Run the full pipeline against my stack and verify the round-trip."
