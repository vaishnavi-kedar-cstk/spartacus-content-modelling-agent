#!/usr/bin/env python3
"""
content-model — Phase 3a of the SAP Spartacus -> Contentstack content-modeling agent.

Reads a normalized inventory (from storefront-extract) + the mapping registry and emits,
STACK-AGNOSTICALLY:
  * content-types.json  — CMA-ready content-type + global-field definitions (registry
    metadata keys stripped), only for the ACTIVE types this site actually uses.
  * entry-plan.json     — every entry to upsert (components deduped by uid, slots, pages),
    the asset plan, and i18n localizations. References are expressed by UPSERT KEY, not by
    a (not-yet-existing) entry uid; cs-seed resolves them at create time.
  * model-report.json   — mapping summary + any UNKNOWN typeCodes (the Gate-1 signal).

Nothing here calls a Contentstack API. Output is a deterministic plan.

Usage:
  python3 model.py --inventory <inv.json> --registry <registry.json> --out <dir>
"""
import argparse, json, os, sys
from collections import OrderedDict

STRIP_PREFIX = "_"  # registry metadata keys


def strip_meta(o):
    """Remove registry-only keys (leading underscore) so the schema is CMA-clean."""
    if isinstance(o, dict):
        return {k: strip_meta(v) for k, v in o.items() if not k.startswith(STRIP_PREFIX)}
    if isinstance(o, list):
        return [strip_meta(v) for v in o]
    return o


def as_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() == "true"
    return bool(v)


def as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def ref(content_type, by, value):
    """A reference placeholder resolved to a real entry uid by cs-seed at create time."""
    return {"_ref": True, "content_type": content_type, "by": by, "value": value}


def asset_ref(code):
    return {"_asset": True, "code": code} if code else None


# ---- per-type field mappers: OCC component dict -> CS entry fields ------------------
BASE_DROP = {"uid", "typeCode", "name", "container"}


def base_meta(c):
    return {"sap_uid": c.get("uid"), "sap_name": c.get("name"),
            "type_code": c.get("typeCode"), "container": as_bool(c.get("container"))}


def map_banner(c):
    m = c.get("media") or {}
    responsive = c.get("typeCode") == "SimpleResponsiveBannerComponent"
    alt = None
    def pick(bp):
        nonlocal alt
        d = m.get(bp) if responsive else (m if bp == "desktop" else None)
        if isinstance(d, dict):
            alt = alt or d.get("altText")
            return asset_ref(d.get("code"))
        return None
    return {
        "media_mobile": pick("mobile"), "media_tablet": pick("tablet"),
        "media_desktop": pick("desktop"), "media_widescreen": pick("widescreen"),
        "alt_text": alt, "url_link": c.get("urlLink"),
        "external": as_bool(c.get("external")),
    }


def map_rich_text(c):
    # RTE JSON is produced from HTML at seed time; carry the source HTML in the plan.
    return {"content": {"_rte_from_html": c.get("content")}}


def map_link(c):
    return {
        "link_name": c.get("linkName"), "url": c.get("url"),
        "content_page": c.get("contentPage"),
        "content_page_label": c.get("contentPageLabelOrId"),
        "target_blank": as_bool(c.get("target")), "external": as_bool(c.get("external")),
        "style_classes": c.get("styleClasses"),
    }


def map_product_carousel(c):
    codes = c.get("productCodes") or ""
    return {"title": c.get("title"),
            "product_codes": [x for x in codes.split() if x],
            "scroll": c.get("scroll"), "popup": as_bool(c.get("popup"))}


def map_merchandising_carousel(c):
    def num(x):
        try:
            return int(x)
        except (TypeError, ValueError):
            return None
    return {"title": c.get("title"), "strategy": c.get("strategy"),
            "number_to_display": num(c.get("numberToDisplay")),
            "scroll": c.get("scroll"),
            "viewport_percentage": num(c.get("viewportPercentage"))}


def map_passthrough(c):
    config = {k: v for k, v in c.items() if k not in BASE_DROP and k != "flexType"}
    return {"flex_type": c.get("flexType"), "config": config}


MAPPERS = {
    "banner": map_banner, "rich_text": map_rich_text, "link": map_link,
    "product_carousel": map_product_carousel,
    "merchandising_carousel": map_merchandising_carousel,
    "passthrough_component": map_passthrough,
}
# Nav components aren't run through a field mapper: their navigationNode tree is
# FLATTENED into a nav_node_flat pool (see Modeler.navigation_entry).
NAV_FLAT_CS_TYPES = {"category_navigation_flat", "footer_navigation_flat"}
# OCC text field -> CS field, for i18n localization of plain strings
I18N_MAP = {
    "content": "content", "title": "title", "linkName": "link_name",
    "urlLink": "url_link", "name": "sap_meta.sap_name", "altText": "alt_text",
    "notice": "config.notice",
}


class Modeler:
    def __init__(self, inventory, registry):
        self.inv = inventory
        self.reg = registry
        self.idx = {k: v for k, v in registry["typecode_index"].items() if k != "_note"}
        self.cts = registry["content_types"]
        self.unknown = OrderedDict()          # typeCode -> observed fields
        self.components = OrderedDict()        # uid -> entry (incl. nav link leaves)
        self.nav_nodes = OrderedDict()         # node_id -> nav_node_flat entry
        self.uid_to_cstype = {}                # component uid -> cs_type
        self.slots = OrderedDict()             # slotId -> entry
        self.pages = OrderedDict()             # label -> entry
        self.assets = OrderedDict()            # code -> asset plan
        self.used_cs_types = set()

    def cs_type_for(self, type_code, observed_fields):
        m = self.idx.get(type_code)
        if not m:
            self.unknown.setdefault(type_code, sorted(observed_fields))
            return "passthrough_component"      # never block; land in passthrough
        return m["cs_type"]

    def add_asset(self, code, manifest):
        if code and code not in self.assets:
            meta = manifest.get(code, {})
            self.assets[code] = {"code": code, "url": meta.get("url"),
                                 "mime": meta.get("mime"), "alt_text": meta.get("altText")}

    def component_entry(self, c):
        uid = c.get("uid")
        if not uid or uid in self.components:
            return uid
        tc = c.get("typeCode")
        cs_type = self.cs_type_for(tc, c.keys())
        if cs_type in NAV_FLAT_CS_TYPES:
            return self.navigation_entry(c, cs_type)
        self.used_cs_types.add(cs_type)
        mapper = MAPPERS.get(cs_type, map_passthrough)
        fields = mapper(c)
        # register any assets the component references
        for v in fields.values():
            if isinstance(v, dict) and v.get("_asset"):
                self.add_asset(v["code"], self.inv.get("mediaManifest", {}))
        entry = {"_content_type": cs_type, "_key": {"field": "sap_meta.sap_uid", "value": uid},
                 "title": c.get("name") or uid, "sap_meta": base_meta(c)}
        entry.update(fields)
        self.components[uid] = entry
        self.uid_to_cstype[uid] = cs_type
        return uid

    def navigation_entry(self, c, cs_type):
        """Flatten a nav component's recursive navigationNode tree into a
        nav_node_flat pool + the flat component entry (all_nodes). The parent
        linkage lives in nav_node_flat.parent_id (plain text), so the depth of
        the menu never affects reference-resolution depth — the whole menu is
        one all_nodes pool the connector reassembles in code."""
        uid = c.get("uid")
        if not uid or uid in self.components:
            return uid
        self.used_cs_types.update({cs_type, "nav_node_flat"})
        root = c.get("navigationNode") or {}
        node_ids = []
        # The root node is a pure container; its children are the top-level items
        # (parent_id = "").
        for i, child in enumerate(root.get("children") or [], start=1):
            self._emit_nav_node(child, "", i, node_ids)
        entry = {
            "_content_type": cs_type,
            "_key": {"field": "sap_meta.sap_uid", "value": uid},
            "title": c.get("name") or uid, "sap_meta": base_meta(c),
            "wrap_after": as_int(c.get("wrapAfter")),
            "all_nodes": [ref("nav_node_flat", "node_id", nid) for nid in node_ids],
        }
        self.components[uid] = entry
        self.uid_to_cstype[uid] = cs_type
        return uid

    def _emit_nav_node(self, node, parent_id, sort_order, collected):
        """Emit one nav_node_flat entry (and its link leaves), then recurse into
        children carrying this node's id as their parent_id."""
        nid = node.get("uid")
        if not nid:
            return
        link_refs = []
        for e in node.get("entries") or []:
            item_id = e.get("itemId") if isinstance(e, dict) else None
            if item_id:
                self._nav_link_entry(item_id)
                link_refs.append(ref("link", "sap_meta.sap_uid", item_id))
        # SAP leaf category nodes carry no title; fall back to the resolved link
        # name (from the nav_resolve pass), else the node id.
        title = node.get("title")
        if not title:
            first = next((e.get("itemId") for e in (node.get("entries") or [])
                          if isinstance(e, dict) and e.get("itemId")), None)
            title = (self.inv.get("navigationLinks", {}).get(first) or {}).get("link_name")
        self.nav_nodes[nid] = {
            "_content_type": "nav_node_flat", "_key": {"field": "node_id", "value": nid},
            "title": title or nid, "node_id": nid, "parent_id": parent_id,
            "sort_order": sort_order, "links": link_refs,
        }
        collected.append(nid)
        for i, child in enumerate(node.get("children") or [], start=1):
            self._emit_nav_node(child, nid, i, collected)

    def _nav_link_entry(self, item_id):
        """Emit a `link` entry for a nav link leaf (from the resolved
        navigationLinks detail), keyed by sap_uid so nav_node_flat.links can
        reference it. Deduped against components already emitted."""
        if item_id in self.components:
            return
        d = (self.inv.get("navigationLinks", {}) or {}).get(item_id) or {}
        self.used_cs_types.add("link")
        self.components[item_id] = {
            "_content_type": "link", "_key": {"field": "sap_meta.sap_uid", "value": item_id},
            "title": d.get("name") or d.get("link_name") or item_id,
            "sap_meta": {"sap_uid": item_id, "sap_name": d.get("name"),
                         "type_code": "CMSLinkComponent", "container": False},
            "link_name": d.get("link_name"), "url": d.get("url"),
            "target_blank": as_bool(d.get("target")), "external": as_bool(d.get("external")),
        }
        self.uid_to_cstype[item_id] = "link"

    def slot_entry(self, s):
        sid = s.get("slotId")
        if not sid or sid in self.slots:
            return sid
        comp_refs = []
        for c in s.get("components", []):
            uid = self.component_entry(c)
            if uid:
                comp_refs.append(ref(self.uid_to_cstype[uid], "sap_meta.sap_uid", uid))
        self.slots[sid] = {
            "_content_type": "cms_content_slot", "_key": {"field": "slot_id", "value": sid},
            "title": s.get("slotId"), "slot_id": sid, "position": s.get("position"),
            "shared": as_bool(s.get("shared")), "components": comp_refs}
        return sid

    def page_entry(self, label, p):
        slot_group = []
        for s in p.get("slots", []):
            sid = self.slot_entry(s)
            if sid:
                slot_group.append({"position": s.get("position"),
                                   "slot": ref("cms_content_slot", "slot_id", sid)})
        self.used_cs_types.update({"cms_page", "cms_content_slot"})
        self.pages[label] = {
            "_content_type": "cms_page", "_key": {"field": "page_label", "value": p.get("label") or label},
            "title": p.get("title") or label, "sap_name": p.get("title") or label,
            "page_label": p.get("label") or label, "page_id": p.get("uid"),
            "template": p.get("template"), "page_type": p.get("typeCode"),
            "robot_tag": p.get("robotTag"), "slots": slot_group}

    def apply_i18n(self):
        """Attach localized field values to component entries by uid."""
        loc = OrderedDict()  # uid -> {lang -> {cs_field: value}}
        for lang, comps in (self.inv.get("i18n") or {}).items():
            for uid, fields in comps.items():
                if uid not in self.components:
                    continue
                mapped = {}
                for occ_f, val in fields.items():
                    cs_f = I18N_MAP.get(occ_f)
                    if cs_f:
                        mapped[cs_f] = val
                if mapped:
                    loc.setdefault(uid, {})[lang] = mapped
        for uid, langs in loc.items():
            self.components[uid]["_localized"] = langs
        return sum(len(v) for v in loc.values())

    def run(self):
        # walk content pages and sampled layouts
        for label, p in self.inv.get("contentPages", {}).items():
            self.page_entry(label, p)
        for label, p in self.inv.get("layoutSamples", {}).items():
            self.page_entry(f"__layout__{label}", p)
        n_loc = self.apply_i18n()

        # content-type definitions: only ACTIVE types actually used, plus spine + global field
        active_used = []
        for uid in ["cms_page", "cms_content_slot"] + sorted(self.used_cs_types):
            ct = self.cts.get(uid)
            if not ct or ct.get("_active") is False:
                continue
            if uid not in [c["uid"] for c in active_used]:
                active_used.append(strip_meta(ct))
        content_types = {
            "global_fields": [strip_meta(gf) for gf in self.reg.get("global_fields", {}).values()],
            "content_types": active_used,
        }

        entry_plan = {
            "site": self.inv.get("site"),
            "languages": self.inv.get("languages"),
            "assets": list(self.assets.values()),
            "entries": {
                # nav_nodes and link leaves must be created before the nav
                # component that references them (all_nodes) and before slots.
                "components": list(self.components.values()),
                "nav_nodes": list(self.nav_nodes.values()),
                "slots": list(self.slots.values()),
                "pages": list(self.pages.values()),
            },
        }
        report = {
            "site": self.inv.get("site"),
            "registry_version": self.reg["meta"]["version"],
            "counts": {
                "content_types_emitted": len(active_used),
                "global_fields": len(content_types["global_fields"]),
                "component_entries": len(self.components),
                "nav_node_entries": len(self.nav_nodes),
                "slot_entries": len(self.slots),
                "page_entries": len(self.pages),
                "assets": len(self.assets),
                "localized_component_variants": n_loc,
            },
            "cs_types_used": sorted(self.used_cs_types | {"cms_page", "cms_content_slot"}),
            "unknown_type_codes": self.unknown,     # -> Gate 1 in the agent
        }
        return content_types, entry_plan, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", required=True)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    inv = json.load(open(a.inventory))
    reg = json.load(open(a.registry))
    os.makedirs(a.out, exist_ok=True)

    content_types, entry_plan, report = Modeler(inv, reg).run()
    json.dump(content_types, open(os.path.join(a.out, "content-types.json"), "w"), indent=2)
    json.dump(entry_plan, open(os.path.join(a.out, "entry-plan.json"), "w"), indent=2)
    json.dump(report, open(os.path.join(a.out, "model-report.json"), "w"), indent=2)

    c = report["counts"]
    print(f"[content-model] {report['site']} (registry v{report['registry_version']})")
    print(f"  content types: {c['content_types_emitted']}  global fields: {c['global_fields']}")
    print(f"  entries: {c['component_entries']} components, {c['nav_node_entries']} nav nodes, "
          f"{c['slot_entries']} slots, {c['page_entries']} pages")
    print(f"  assets: {c['assets']}  localized variants: {c['localized_component_variants']}")
    if report["unknown_type_codes"]:
        print("  GATE 1 — unknown typeCodes routed to passthrough:",
              list(report["unknown_type_codes"]))
    else:
        print("  no unknown typeCodes ✓")
    print(f"  -> {a.out}/content-types.json, entry-plan.json, model-report.json")


if __name__ == "__main__":
    main()
