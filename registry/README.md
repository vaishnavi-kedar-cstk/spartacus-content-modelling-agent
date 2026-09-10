# SAP Spartacus → Contentstack — Mapping Registry

**Version 0.1.0 · draft · derived from live `electronics-spa` + `powertools-spa`**

The registry is the crown-jewel asset of the content-modeling agent. It is the single
source of truth for *how a SAP Spartacus storefront's content becomes Contentstack
content types*. The `content-model` skill reads it to turn a crawled inventory into
schemas; the `cs-seed` skill reads it to create those content types and upsert entries.

Everything here is authored from real crawled data — no invented fields.

---

## 1. The two-layer model

| Layer | What it is | Stability |
|---|---|---|
| **Structural spine** | `cms_page` → `cms_content_slot` → component references | **Fixed.** Identical across every storefront tested (9/9 templates, 16 global slots). |
| **Component registry** | one content type per meaningful `typeCode`, plus a passthrough | **Slow-growing.** ~1 new type per storefront. |

This mirrors SAP's own `Page → ContentSlot → Component` structure 1:1 — which is
exactly why the round-trip (§4) can be exact.

### Spine at a glance

```
cms_page
  ├─ page_label            (unique — upsert key)
  ├─ template, page_type, robot_tag
  └─ slots[]  (ordered)
       ├─ position         (e.g. "Section1", "Footer")
       └─ slot ──────────► cms_content_slot
                             ├─ slot_id      (unique — upsert key)
                             ├─ shared        (true = global header/footer/nav)
                             └─ components[] ─► [ banner | rich_text | link |
                                                 product_carousel | merchandising_carousel |
                                                 navigation | passthrough_component ]  (ordered)
```

**Shared slots are not a separate type.** A global footer is one `cms_content_slot`
entry with `shared = true`, referenced by every page. Model once, reference everywhere.

---

## 2. typeCode → Contentstack content type

All 17 component types seen across the two sites are mapped. `authored` types get a
full schema; `functional` types collapse into `passthrough_component` (lossless).

| SAP typeCode | Contentstack type | Class |
|---|---|---|
| `SimpleResponsiveBannerComponent` | `banner` | authored |
| `SimpleBannerComponent` | `banner` | authored |
| `CMSParagraphComponent` | `rich_text` | authored |
| `CMSLinkComponent` | `link` | authored |
| `ProductCarouselComponent` | `product_carousel` | authored |
| `MerchandisingCarouselComponent` | `merchandising_carousel` | authored *(electronics only)* |
| `FooterNavigationComponent` | `passthrough_component` → `navigation` in v0.2 | functional *(deferred)* |
| `CategoryNavigationComponent` | `passthrough_component` → `navigation` in v0.2 | functional *(deferred)* |
| `CMSFlexComponent` (+ 8 others) | `passthrough_component` | functional |

The 8 others folded into passthrough: `CMSSiteContextComponent`, `MiniCartComponent`,
`SearchBoxComponent`, `BreadcrumbComponent`, `ProductRefinementComponent`,
`SearchResultsListComponent`, `CartSuggestionComponent`, `JspIncludeComponent`.

> **v0.1 nav decision:** the `navigation` / `navigation_node` schemas are defined but
> `_active: false`. Footer/category nav route to `passthrough_component` for now (nothing
> blocks; the resolved link tree isn't captured yet). They activate in v0.2 when
> `storefront-extract` adds the `CMSNavigationNode` resolve pass. `cs-seed` skips any
> content type with `_active: false`.

---

## 3. Idempotent upsert keys

Re-running the seed must *update*, never duplicate. Every write upserts by a stable,
unique key sourced from SAP:

| Content type | Upsert key | SAP source |
|---|---|---|
| `cms_page` | `page_label` | page route label |
| `cms_content_slot` | `slot_id` | `slotId` |
| every component type | `sap_meta.sap_uid` | component `uid` |
| `navigation_node` | `node_id` | node `uid` |

"Update the seed data" is therefore the *same operation* as "seed it."

---

## 4. The round-trip contract

A model is only valid if the connector can turn a seeded entry **back** into the OCC
component shape and it diffs clean against the original inventory. The registry records
what the connector must emit per `type_code` (the `_round_trip` key on each type):

- **`banner`** — reads `type_code`. `SimpleResponsiveBannerComponent` → emit
  `media{mobile,tablet,desktop,widescreen}`; `SimpleBannerComponent` → single `media` + `external`.
- **`rich_text`** — emit the HTML serialization as `content`.
- **`link`** — emit `linkName`, `url` *or* `contentPage`/`contentPageLabelOrId`, `target`, `external`, `styleClasses`.
- **`product_carousel`** — join `product_codes[]` with a single space → `productCodes`.
- **`navigation`** — `type_code` picks footer vs category; emit `navigationNode` + `wrapAfter` (+ footer's `notice`, `showLanguageCurrency`).
- **`passthrough_component`** — emit `type_code` and spread `config{}` back as the component's fields.

`type_code` (carried on every component via the `sap_component_base` global field) is
the switch the connector reads. This is the coupling point between registry and connector:
**if the connector's expected shape changes, the matching `_round_trip` entry changes with it.**

---

## 5. Field-level decisions to confirm

These are real judgment calls in the draft — flagged for review, not silently chosen.

- **RTE fidelity (`rich_text`).** ✅ **Resolved: author in advanced JSON RTE.** Seed maps
  OCC HTML → RTE JSON; deliver serializes RTE JSON → HTML. The round-trip diff normalizes
  HTML (whitespace, attribute order) so benign serialization differences aren't false
  mismatches. Watch this pairing in the first verify run — it's the one field where
  import/export conversion could drift.
- **`product_codes` as array.** Modeled as a multiple-text field (cleaner authoring);
  seed splits on space, connector re-joins. Alternative: keep the raw string.
- **Banner unification.** One `banner` type covers both simple and responsive banners,
  distinguished by `type_code`, with optional per-breakpoint assets. Alternative: two types.
- **Media as Contentstack assets.** Banner media become real CS assets (uploaded from the
  OCC `?context=` byte stream); `alt_text` is kept as a field. The connector maps the CS
  asset URL back to `media.url`.
- **Navigation node tree — not yet crawled.** `navigation` + `navigation_node` schemas
  are defined, but the current crawler does **not** resolve the `CMSNavigationNode` link
  tree. `storefront-extract` (Phase 2) needs a dedicated nav-resolve pass before these
  types can be seeded with real data. Until then, footer/category nav can land in
  `passthrough_component` so nothing blocks.

---

## 6. Extending the registry (Gate 1)

When `content-model` meets a `typeCode` not in `typecode_index`, the agent does **not**
guess. It:

1. Routes the component to `passthrough_component` so the run never blocks and no data is lost.
2. Proposes a mapping (suggested class + candidate fields from the observed shape).
3. Pauses at **Gate 1** for a human to approve/adjust.
4. On approval, adds the new content type (if authored) and a `typecode_index` row with
   `since` = the new registry version and `seen_on` = the site — and the mapping is now
   available to **every** future run and every other team.

This is what makes onboarding a new storefront mostly *recognition*: the agent surfaces
only the 1–2 genuinely new types.

---

## 7. Versioning & files

- `registry.json` — authoritative, machine-readable. Consumed by the skills.
- `README.md` — this human spec.
- **SemVer:** patch = field tweak; minor = new component type / new typeCode row;
  major = spine change or a breaking round-trip change.
- Underscore-prefixed keys (`_source`, `_round_trip`, `_note`, `_flag`, `_class`,
  `_covers`, `_layer`, `_decision`) are registry metadata — `cs-seed` strips them before
  calling the Contentstack Management API.

---

## 8. Decisions — resolved for v0.1

1. **RTE vs raw HTML** → ✅ author in JSON RTE (§5).
2. **Staged vs Online** → ✅ seed the published **`Online`** catalog version.
3. **Navigation** → ✅ **defer** to `passthrough_component`; activate `navigation` in v0.2
   with the nav resolve pass.
4. **Registry location** → ✅ lives at `SAP/content-modeling-agent/registry/` in the project.

Carried into later phases: confirm the RTE↔HTML pairing survives the first verify run;
schedule the v0.2 nav resolve pass in `storefront-extract`.
