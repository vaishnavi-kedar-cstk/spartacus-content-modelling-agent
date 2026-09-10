# verify

**Phase 4 skill · the check step.** Two layers.

## Layer 1 — data round-trip (the correctness proof) · `roundtrip.py`

Reads seeded entries back from the live stack, reverse-maps each component to its original
OCC shape (the registry's per-type `_round_trip` contract), and diffs against the original
inventory. If it diffs clean, the Contentstack model **faithfully represents** the
storefront content — correct by construction. No storefront required.

```bash
export CS_API_KEY=...  CS_MANAGEMENT_TOKEN=...
python3 roundtrip.py --inventory ../../fixtures/electronics-spa.inventory.json --verbose
```

What it checks per component type: banner media codes (resolved via asset titles),
rich-text HTML (whitespace-normalized), link fields, carousel title/codes/scroll, and
passthrough `type_code` + full `config`. Reports per-type match counts and any mismatch
with an orig-vs-cs field diff.

**Result on electronics-spa:** 78/78 components match across 16 types — PASS.

## Layer 1b — seed integrity · `verify_seed.py`

Confirms entry counts per content type match the plan and that a page's slot→component
references resolve to real entry uids (no dangling references).

```bash
python3 verify_seed.py --model-dir <model-dir> --page homepage
```

## Layer 1c — connector round-trip (incl. navigation) · `verify_connector.py`

The connector-profile round-trip (used by the governed pipeline). Reads seeded authored
components back and diffs each against its original OCC shape, then verifies **flat
navigation**: for every Category/Footer nav component it walks the seeded `all_nodes` pool,
reassembles the tree from `nav_node_flat.node_id`/`parent_id` (exactly as the connector does
at runtime), and diffs each node's parent / `sort_order` / link against the source
`navigationNode` tree. Proves the flat model faithfully represents the source hierarchy — a
reparented, misordered, or missing node fails the check.

```bash
python3 verify_connector.py --inventory <fixture.json> --plan <connector-plan.json> --verbose
```

## Layer 2 — rendered round-trip (storefront)

A running Spartacus storefront pointed at the stack, rendering pages, compared (Live
Preview / DOM / screenshot) against the original OCC site. This is a separate integration
task with real prerequisites:

1. **Publish** — the stack needs an environment + delivery token, and entries must be
   published (this phase seeded to management/draft only).
2. **A connector that speaks this schema** — mapping `cms_page → slots → components` and
   each component type back into Spartacus CMS component data. The `_round_trip` contract
   in the registry is the spec for that mapping; Layer 1 already proves the mapping is
   lossless in the data direction.
3. **Wire + run** the storefront against the stack and diff rendered pages vs the OCC site.

Layer 1 is the rigorous content-fidelity gate; Layer 2 is the end-user-visible confirmation.
