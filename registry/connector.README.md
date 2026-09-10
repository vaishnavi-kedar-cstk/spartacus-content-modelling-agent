# Connector Mapping Profile — `connector.registry.json`

**v0.3.0 · the go-forward model.** Maps OCC inventory onto the **shipped content types of
the `contentstack-spartacus-feature` connector** (the stable, tested reference-field model).

This replaces the free-form `registry.json` (v0.2.1) as the target for seeding. `registry.json`
is kept for reference only.

## Why this exists

The connector reads pages as **reference fields** (not modular blocks — verified in
`contentstack-cms-page.normalizer.ts` + `slot-maps.ts`). It ships its own content model as a
starter pack (`contentstack-spartacus-feature/import-export/starter-pack/content_types/`) with a
`CONTENT-MODEL.md` spec. To make the connector render our seeded data, we must conform to that
model rather than invent our own.

## The model (hybrid "islands")

OCC renders the base of **every** page; Contentstack overrides **only the slots you author**.
Functional components, unauthored slots, and non-content pages stay in OCC — **not seeded**.
Unit of control = the slot.

- **Pages** — per template, keyed by a `url` slug (home = `/`):
  `landing_page`, `content_page`, `product_page`, `category_page`. Other templates
  (Login/Account/Cart/Checkout/SearchResults/Error/StoreFinder) → left to OCC, no CS page.
- **Slots** — named reference fields **directly on the page** (`section1`, `body_content`,
  `footer`…). No slot-entry type. Position→field via `slot_field_map`.
- **Shell** — one `global_slots` entry (`site_logo`, `search_box`, `footer`…) merged into every page.
- **Components** (authored only): `simple_responsive_banner_component`,
  `simple_banner_component`, `product_carousel_component` (field `products`),
  `cms_paragraph_component` (`content`), `cms_link_component` (`link_name`/`url`/`target`),
  `cms_flex_component` (`flex_type`). Navigation deferred (needs `nav_node` tree).
- **Functional** (mini-cart, search box, breadcrumb, merch carousel, product-page widgets…)
  → `occ_only_types`, never seeded.

## Content types come from the connector, not from us

This profile is a **translation table only**. The content-type schemas are the connector's —
import them from the starter pack (csdx `cm:stacks:import` or POST the JSON files). Our seeder
references those uids; it does not (re)define them.

## Idempotency keys

- Pages: `url` (the slug).
- Component + global_slots entries: `title` (set to the SAP component uid, which is unique).

## Scope this profile produces (validated on fixtures)

| Site | Authored pages | Authored component entries | Left to OCC |
|---|---|---|---|
| electronics-spa | 4 (home + faq/contact/sale) | 29 | 12 pages + all functional |
| powertools-spa | 4 | 28 | 11 pages + all functional |

## Deferred

- **Navigation** (`category_navigation_component`, `footer_navigation_component`, `nav_node`
  tree) — v0.3.1, same as the v1 nav deferral. The extractor already captures the nav tree.
- **Product/Category page layouts** — `product_page`/`category_page` types exist; whether to
  author them or leave PDP/PLP entirely to OCC is a seed-scope choice (CONTENT-MODEL §5 leans
  marketing-only).
