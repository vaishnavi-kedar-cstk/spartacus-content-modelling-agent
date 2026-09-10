# Flat navigation content model

A sample content model for storefront navigation (footer, category/header menus)
that **never hits the Delivery API's reference-depth limit**, no matter how deep
the menu goes.

## The idea in one line

> Don't nest nodes inside nodes. Make **every menu item its own entry** and let it
> point at its parent by a plain text id. Fetch the whole menu in **one flat query**
> and rebuild the tree in code.

Nested model (the problem): a 4-level menu needs the API to resolve 4+ levels of
references in a single call. That limit is capped by your Contentstack plan — deep
menus silently lose their bottom levels, or the whole query is rejected.

Flat model (this sample): the tree structure lives in text fields, so building it
costs **zero** reference depth. The only reference is the link itself — one hop,
always resolves. **Menu depth stops mattering.**

## The content type: `nav_node`

Five fields. Import `nav_node.content-type.json` to create it.

| Field         | Type                        | What it's for                                              |
|---------------|-----------------------------|------------------------------------------------------------|
| `title`       | Text                        | The label shown in the menu (e.g. "Company")               |
| `menu_id`     | Text                        | Which menu this belongs to (e.g. `footer`, `main-category`) |
| `node_id`     | Text (unique)               | This node's own stable id — children point back to it       |
| `parent_id`   | Text (empty = top level)    | The `node_id` of this node's parent                         |
| `sort_order`  | Number                      | Position among its siblings (1, 2, 3 …)                     |
| `links`       | Reference → `cms_link_component` | The clickable link(s) under this node (leaf nodes only) |

**Rule of thumb:**
- A **heading** ("Company") has children and no `links`.
- A **leaf** ("About Us") has a `link` and no children.
- A **top-level** item has an empty `parent_id`.

## Worked example — a footer with two sections

What it looks like to the shopper:

```
Company            Help
  About Us           Contact
  Careers            Returns
```

Modeled as **6 flat `nav_node` entries** — all with `menu_id = "footer"`:

| title    | node_id           | parent_id       | sort_order | links            |
|----------|-------------------|-----------------|------------|------------------|
| Company  | `footer-company`  | *(empty)*       | 1          | —                |
| Help     | `footer-help`     | *(empty)*       | 2          | —                |
| About Us | `footer-about`    | `footer-company`| 1          | → About link     |
| Careers  | `footer-careers`  | `footer-company`| 2          | → Careers link   |
| Contact  | `footer-contact`  | `footer-help`   | 1          | → Contact link   |
| Returns  | `footer-returns`  | `footer-help`   | 2          | → Returns link   |

One entry as raw JSON (see `footer.entries.example.json` for all six):

```json
{
  "title": "About Us",
  "menu_id": "footer",
  "node_id": "footer-about",
  "parent_id": "footer-company",
  "sort_order": 1,
  "links": [ { "uid": "<about_link_entry_uid>", "_content_type_uid": "cms_link_component" } ]
}
```

## How it's fetched and rebuilt

**Fetch — one query, one reference level:**

```
contentType("nav_node")
  .query()
  .where("menu_id", "footer")
  .includeReference(["links"])   // the only include — always 1 deep
  .find()
```

**Rebuild the tree in code (~10 lines):**

```js
function buildTree(nodes) {
  const byParent = {};
  for (const n of nodes) (byParent[n.parent_id || ""] ??= []).push(n);
  for (const list of Object.values(byParent))
    list.sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));

  const attach = (parentId) =>
    (byParent[parentId] || []).map((n) => ({
      title: n.title,
      links: n.links,
      children: attach(n.node_id),   // group children by this node's id
    }));

  return attach("");                 // "" = the top-level items
}
```

Depth 2 or depth 20, the fetch is identical. There is no plan limit to tune, no
include depth to get wrong, and nothing silently dropped.

## Applying it to a category menu

Exactly the same — just more levels of nodes linked by `parent_id`:

```
menu_id = "main-category"
  Cameras            (parent_id empty)
    Digital Cameras  (parent_id = cameras)
      Compact        (parent_id = digital-cameras, has a link)
```

The entry count grows; the fetch and the reference depth do **not**.

See `powertools-category.entries.example.json` for a full depth-4 category menu
(17 nodes) mapped this way — the same pattern as the footer, just more rows.

## One sample, every storefront

This model is depth- and storefront-agnostic. Footer, header, or category menu;
electronics-spa, powertools, or any future demo — they all reduce to the same flat
`nav_node` rows. Two worked examples ship here as the reference:

- `footer.entries.example.json` — simple, shallow (2 sections)
- `powertools-category.entries.example.json` — deep (4 levels, wrapper nodes)
