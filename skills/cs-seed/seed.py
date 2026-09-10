#!/usr/bin/env python3
"""
cs-seed — Phase 3b of the SAP Spartacus -> Contentstack content-modeling agent.

Reads the content-model output (content-types.json + entry-plan.json) and produces an
ORDERED, IDEMPOTENT seed plan. Default mode is DRY-RUN: it emits seed-plan.json and a
human summary and calls no API. Live seeding (--execute) is guarded behind credentials
and intentionally not wired in the stack-agnostic phase (see _execute()).

Ordering guarantees the plan can be applied without dangling references:
  global field -> content types (topologically) -> assets -> component entries
  -> slot entries -> page entries -> localizations -> (optional) publish.

Idempotency: every write is an UPSERT keyed by a stable unique field
  (page_label / slot_id / sap_meta.sap_uid for entries; sap_code tag for assets;
   uid for content types & global fields). Re-running updates, never duplicates.

Usage:
  python3 seed.py --model-dir <dir> [--out seed-plan.json]
  python3 seed.py --model-dir <dir> --execute      # requires CS_* env; not enabled yet
"""
import argparse, json, os, sys
from collections import OrderedDict


def topo_content_types(content_types):
    """Order content types so every reference target / global field precedes its user."""
    by_uid = {c["uid"]: c for c in content_types}
    ordered, seen = [], set()

    def deps(ct):
        d = set()

        def scan(fields):
            for f in fields:
                if f.get("data_type") == "reference":
                    for t in (f.get("reference_to") or []):
                        if t in by_uid:
                            d.add(t)
                # references can be nested inside group / modular-block schemas
                if f.get("data_type") in ("group", "blocks") and f.get("schema"):
                    scan(f["schema"])
            # global_field references live outside content_types -> prior stage
        scan(ct.get("schema", []))
        return d

    def visit(uid, stack):
        if uid in seen:
            return
        if uid in stack:            # cycle guard (spine has none)
            return
        stack.add(uid)
        for d in deps(by_uid[uid]):
            visit(d, stack)
        stack.discard(uid)
        seen.add(uid)
        ordered.append(uid)

    for uid in by_uid:
        visit(uid, set())
    return ordered


def build_plan(model):
    ct = model["content_types"]
    ep = model["entry_plan"]
    ops = []

    def add(op, kind, target, key, depends=None, extra=None):
        o = {"seq": len(ops) + 1, "op": op, "kind": kind, "target": target,
             "key": key, "mode": "UPSERT"}
        if depends:
            o["depends_on"] = depends
        if extra:
            o.update(extra)
        ops.append(o)

    # 1) global fields
    for gf in ct.get("global_fields", []):
        add("upsert_global_field", "global_field", gf["uid"], f"uid={gf['uid']}")

    # 2) content types, topologically ordered
    order = topo_content_types(ct.get("content_types", []))
    gf_uids = [g["uid"] for g in ct.get("global_fields", [])]
    for uid in order:
        add("upsert_content_type", "content_type", uid, f"uid={uid}",
            depends=(["global_field:" + g for g in gf_uids] or None))

    # 3) assets
    for a in ep.get("assets", []):
        add("upsert_asset", "asset", a["code"], f"sap_code={a['code']}",
            extra={"download_url": a.get("url"), "mime": a.get("mime")})

    # 4) component entries (depend on their content type + referenced assets +,
    #    for nav components, the flat node pool they reference via all_nodes)
    NAV_CTS = {"category_navigation_flat", "footer_navigation_flat"}

    def emit_component(e):
        asset_codes = [v["code"] for v in e.values()
                       if isinstance(v, dict) and v.get("_asset")]
        dep = ["content_type:" + e["_content_type"]] + ["asset:" + c for c in asset_codes]
        # nav component -> its flat node pool (all_nodes); no-op for other types
        dep += ["entry:" + r["value"] for r in (e.get("all_nodes") or [])
                if isinstance(r, dict) and r.get("_ref")]
        add("upsert_entry", e["_content_type"], e["_key"]["value"],
            f"{e['_key']['field']}={e['_key']['value']}", depends=dep)
        for lang in (e.get("_localized") or {}):
            add("localize_entry", e["_content_type"], f"{e['_key']['value']}@{lang}",
                f"{e['_key']['field']}={e['_key']['value']} lang={lang}",
                depends=[f"entry:{e['_key']['value']}"])

    # 4a) non-nav components first — this includes the `link` leaves that flat nav
    #     nodes reference, so they exist before step 4b.
    for e in ep["entries"]["components"]:
        if e["_content_type"] not in NAV_CTS:
            emit_component(e)

    # 4b) flat nav nodes — each depends on the link leaves in its `links` field.
    for e in ep["entries"].get("nav_nodes", []):
        dep = ["content_type:nav_node_flat"] + [
            "entry:" + r["value"] for r in (e.get("links") or [])
            if isinstance(r, dict) and r.get("_ref")]
        add("upsert_entry", "nav_node_flat", e["_key"]["value"],
            f"{e['_key']['field']}={e['_key']['value']}", depends=dep)

    # 4c) nav components last — depend on every node in their all_nodes pool.
    for e in ep["entries"]["components"]:
        if e["_content_type"] in NAV_CTS:
            emit_component(e)

    # 5) slot entries (depend on component entries they reference)
    for e in ep["entries"]["slots"]:
        dep = ["content_type:cms_content_slot"] + [
            "entry:" + r["value"] for r in e.get("components", []) if r.get("_ref")]
        add("upsert_entry", "cms_content_slot", e["_key"]["value"],
            f"slot_id={e['_key']['value']}", depends=dep)

    # 6) page entries (depend on slot entries)
    for e in ep["entries"]["pages"]:
        dep = ["content_type:cms_page"] + [
            "entry:" + s["slot"]["value"] for s in e.get("slots", []) if s.get("slot")]
        add("upsert_entry", "cms_page", e["_key"]["value"],
            f"page_label={e['_key']['value']}", depends=dep)

    return ops


def summarize(ops):
    from collections import Counter
    by_op = Counter(o["op"] for o in ops)
    by_ct = Counter(o["target"] if o["op"] == "upsert_content_type" else None for o in ops)
    return by_op, {k: v for k, v in by_ct.items() if k}


def _execute(ops):
    key = os.environ.get("CS_API_KEY")
    tok = os.environ.get("CS_MANAGEMENT_TOKEN")
    if not (key and tok):
        sys.exit("--execute needs CS_API_KEY and CS_MANAGEMENT_TOKEN in the environment.")
    sys.exit("Live seeding is not enabled in the stack-agnostic phase (Phase 3b). "
             "The ordered UPSERT plan in seed-plan.json is ready to apply once a target "
             "stack is provisioned and the CMA client is wired.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True, help="dir with content-types.json + entry-plan.json")
    ap.add_argument("--out", default="seed-plan.json")
    ap.add_argument("--execute", action="store_true", help="apply live (guarded; not enabled)")
    a = ap.parse_args()

    model = {
        "content_types": json.load(open(os.path.join(a.model_dir, "content-types.json"))),
        "entry_plan": json.load(open(os.path.join(a.model_dir, "entry-plan.json"))),
    }
    ops = build_plan(model)

    if a.execute:
        _execute(ops)
        return

    out_path = a.out if os.path.isabs(a.out) else os.path.join(a.model_dir, a.out)
    json.dump({"mode": "dry-run", "site": model["entry_plan"].get("site"),
               "total_operations": len(ops), "operations": ops},
              open(out_path, "w"), indent=2)

    by_op, by_ct = summarize(ops)
    print(f"[cs-seed] DRY-RUN — {model['entry_plan'].get('site')}  ({len(ops)} ordered UPSERT ops)")
    for op in ["upsert_global_field", "upsert_content_type", "upsert_asset",
               "upsert_entry", "localize_entry"]:
        if by_op.get(op):
            print(f"  {by_op[op]:4d}  {op}")
    print(f"  content types (in dependency order): {topo_content_types(model['content_types']['content_types'])}")
    # cheap integrity check: every entry dependency points at an op we actually emit
    keys = {o["op"] == "upsert_entry" and o["target"] for o in ops}
    entry_targets = {o["target"] for o in ops if o["op"] == "upsert_entry"}
    ct_targets = {o["target"] for o in ops if o["op"] == "upsert_content_type"}
    asset_targets = {o["target"] for o in ops if o["op"] == "upsert_asset"}
    dangling = []
    for o in ops:
        for d in o.get("depends_on", []):
            kind, _, val = d.partition(":")
            pool = {"entry": entry_targets, "content_type": ct_targets,
                    "asset": asset_targets, "global_field": {g for g in [
                        gf["uid"] for gf in model["content_types"]["global_fields"]]}}.get(kind)
            if pool is not None and val not in pool:
                dangling.append(d)
    print(f"  dangling dependencies: {len(dangling)} {'✓' if not dangling else dangling[:5]}")
    print(f"  -> {out_path}")


if __name__ == "__main__":
    main()
