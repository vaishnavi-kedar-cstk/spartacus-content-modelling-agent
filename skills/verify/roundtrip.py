#!/usr/bin/env python3
"""
roundtrip — data-level round-trip verification (Phase 4, the correctness proof).

Reads the seeded entries back from the live stack, reverse-maps each component to its
original OCC shape (implementing the registry's per-type _round_trip contract), and diffs
against the original inventory (the fixture). If it diffs clean, the Contentstack model
faithfully represents the storefront content -- the model is correct by construction.

This is the DATA layer of the connector round-trip. It does not render a storefront; it
proves the content survives CS storage and can be mapped back to what SAP served.

Read-only. Creds from env: CS_API_KEY, CS_MANAGEMENT_TOKEN, CS_REGION_BASE.

Usage: python3 roundtrip.py --inventory <fixture.json> [--verbose]
"""
import argparse, json, os, re, sys, urllib.parse, urllib.request
from collections import defaultdict

API = os.environ.get("CS_REGION_BASE", "https://api.contentstack.io").rstrip("/")
KEY, TOKEN = os.environ.get("CS_API_KEY"), os.environ.get("CS_MANAGEMENT_TOKEN")


def cma(path):
    req = urllib.request.Request(f"{API}/v3{path}")
    req.add_header("api_key", KEY); req.add_header("authorization", TOKEN)
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode())


def all_entries(ct):
    out, skip = [], 0
    while True:
        r = cma(f"/content_types/{ct}/entries?limit=100&skip={skip}&include_count=true")
        es = r.get("entries") or []
        out += es
        skip += len(es)
        if len(es) < 100 or skip >= (r.get("count") or 0):
            break
    return out


def asset_titles():
    out, skip = {}, 0
    while True:
        r = cma(f"/assets?limit=100&skip={skip}&include_count=true")
        a = r.get("assets") or []
        for x in a:
            out[x["uid"]] = x.get("title")
        skip += len(a)
        if len(a) < 100 or skip >= (r.get("count") or 0):
            break
    return out


def as_bool(v):
    return v.strip().lower() == "true" if isinstance(v, str) else bool(v)


def norm_html(s):
    return re.sub(r"\s+", " ", (s or "").strip())


# ---- comparable projections (what the round-trip must preserve) --------------------
def orig_proj(c):
    tc = c.get("typeCode")
    if tc in ("SimpleResponsiveBannerComponent", "SimpleBannerComponent"):
        m = c.get("media") or {}
        if tc == "SimpleResponsiveBannerComponent":
            codes = [m[b]["code"] for b in ("mobile", "tablet", "desktop", "widescreen")
                     if isinstance(m.get(b), dict) and m[b].get("code")]
        else:
            codes = [m["code"]] if m.get("code") else []
        return {"media": sorted(codes), "url_link": c.get("urlLink"),
                "external": as_bool(c.get("external"))}
    if tc == "CMSParagraphComponent":
        return {"content": norm_html(c.get("content"))}
    if tc == "CMSLinkComponent":
        return {"link_name": c.get("linkName"), "url": c.get("url"),
                "target": as_bool(c.get("target")), "external": as_bool(c.get("external")),
                "style_classes": c.get("styleClasses")}
    if tc == "ProductCarouselComponent":
        return {"title": c.get("title"),
                "product_codes": sorted((c.get("productCodes") or "").split()),
                "scroll": c.get("scroll"), "popup": as_bool(c.get("popup"))}
    if tc == "MerchandisingCarouselComponent":
        return {"title": c.get("title"), "strategy": c.get("strategy"),
                "scroll": c.get("scroll")}
    # functional -> passthrough: preserve typeCode + all non-base fields
    return {"type_code": tc,
            "config": {k: v for k, v in c.items()
                       if k not in ("uid", "typeCode", "name", "container", "flexType")}}


def cs_proj(e, ct, assets):
    if ct == "banner":
        codes = [assets.get(_uid(e.get(f))) for f in
                 ("media_mobile", "media_tablet", "media_desktop", "media_widescreen")
                 if e.get(f)]
        codes = [c for c in codes if c]
        return {"media": sorted(codes), "url_link": e.get("url_link"),
                "external": bool(e.get("external"))}
    if ct == "rich_text":
        return {"content": norm_html(e.get("content"))}
    if ct == "link":
        return {"link_name": e.get("link_name"), "url": e.get("url"),
                "target": bool(e.get("target_blank")), "external": bool(e.get("external")),
                "style_classes": e.get("style_classes")}
    if ct == "product_carousel":
        return {"title": e.get("title"),
                "product_codes": sorted(e.get("product_codes") or []),
                "scroll": e.get("scroll"), "popup": bool(e.get("popup"))}
    if ct == "merchandising_carousel":
        return {"title": e.get("title"), "strategy": e.get("strategy"),
                "scroll": e.get("scroll")}
    meta = e.get("sap_meta") or {}
    cfg = e.get("config")
    try:
        cfg = json.loads(cfg) if isinstance(cfg, str) else (cfg or {})
    except Exception:
        cfg = {}
    return {"type_code": meta.get("type_code"), "config": cfg}


def _uid(v):
    if isinstance(v, str):
        return v
    if isinstance(v, list) and v:
        return v[0] if isinstance(v[0], str) else v[0].get("uid")
    if isinstance(v, dict):
        return v.get("uid")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", required=True)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    if not (KEY and TOKEN):
        sys.exit("set CS_API_KEY and CS_MANAGEMENT_TOKEN")

    inv = json.load(open(a.inventory))

    # original components (primary language), deduped by uid, from content pages
    orig = {}
    for p in inv["contentPages"].values():
        for s in p["slots"]:
            for c in s["components"]:
                if c.get("uid"):
                    orig.setdefault(c["uid"], c)

    # pull seeded entries + asset titles from the stack
    reg = json.load(open(os.path.join(os.path.dirname(__file__), "..", "..", "registry", "registry.json")))
    idx = {k: v for k, v in reg["typecode_index"].items() if k != "_note"}
    comp_cts = sorted({v["cs_type"] for v in idx.values()})
    assets = asset_titles()
    cs_by_uid, cs_ct = {}, {}
    for ct in comp_cts:
        for e in all_entries(ct):
            u = (e.get("sap_meta") or {}).get("sap_uid")
            if u:
                cs_by_uid[u] = e
                cs_ct[u] = ct

    # compare
    per_type = defaultdict(lambda: {"n": 0, "match": 0, "miss": 0})
    mismatches = []
    missing = []
    for uid, c in orig.items():
        tc = c.get("typeCode")
        cs = cs_by_uid.get(uid)
        if not cs:
            missing.append(uid)
            continue
        want = orig_proj(c)
        got = cs_proj(cs, cs_ct[uid], assets)
        rec = per_type[tc]
        rec["n"] += 1
        if want == got:
            rec["match"] += 1
        else:
            rec["miss"] += 1
            diff = {k: {"orig": want.get(k), "cs": got.get(k)}
                    for k in set(want) | set(got) if want.get(k) != got.get(k)}
            mismatches.append({"uid": uid, "type": tc, "diff": diff})

    total = sum(r["n"] for r in per_type.values())
    matched = sum(r["match"] for r in per_type.values())
    print(f"[roundtrip] {inv['site']} @ {API}")
    print(f"  components compared: {total}  matched: {matched}  "
          f"mismatched: {total - matched}  missing-in-stack: {len(missing)}")
    for tc in sorted(per_type):
        r = per_type[tc]
        print(f"    {tc:34s} {r['match']}/{r['n']} match")
    if missing:
        print("  MISSING in stack:", missing[:10])
    if mismatches and a.verbose:
        print("  --- mismatches ---")
        for m in mismatches[:15]:
            print(f"  {m['type']} {m['uid']}")
            for f, d in m["diff"].items():
                print(f"      {f}: orig={json.dumps(d['orig'])[:80]}  cs={json.dumps(d['cs'])[:80]}")
    ok = (matched == total and not missing)
    print("  RESULT:", "PASS — model round-trips faithfully" if ok else f"{total-matched} mismatch(es) to review")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
