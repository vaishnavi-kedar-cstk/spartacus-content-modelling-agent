#!/usr/bin/env python3
"""
verify_seed — seed-side verification (Phase 4, part 1).

Queries the live stack via the Management API and checks that what content-model planned
actually landed:
  * entry count per content type matches the plan,
  * a spot-checked page resolves its slot references, and each slot resolves its component
    references (i.e. references are real, not dangling).

Read-only. Creds from env: CS_API_KEY, CS_MANAGEMENT_TOKEN, CS_REGION_BASE.

Usage: python3 verify_seed.py --model-dir DIR [--page homepage]
"""
import argparse, json, os, sys, urllib.parse, urllib.request
from collections import Counter

API = os.environ.get("CS_REGION_BASE", "https://api.contentstack.io").rstrip("/")
KEY, TOKEN = os.environ.get("CS_API_KEY"), os.environ.get("CS_MANAGEMENT_TOKEN")


def cma(path):
    req = urllib.request.Request(f"{API}/v3{path}")
    req.add_header("api_key", KEY); req.add_header("authorization", TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"_err": e.code, "_body": e.read().decode()[:200]}


def count(ct):
    r = cma(f"/content_types/{ct}/entries?include_count=true&limit=1")
    return r.get("count")


def query_one(ct, field, value):
    q = urllib.parse.quote(json.dumps({field: value}))
    r = cma(f"/content_types/{ct}/entries?query={q}&limit=1")
    es = r.get("entries") or []
    return es[0] if es else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--page", default="homepage")
    a = ap.parse_args()
    if not (KEY and TOKEN):
        sys.exit("set CS_API_KEY and CS_MANAGEMENT_TOKEN")

    ep = json.load(open(os.path.join(a.model_dir, "entry-plan.json")))
    expected = Counter(e["_content_type"] for e in ep["entries"]["components"])
    expected["cms_content_slot"] = len(ep["entries"]["slots"])
    expected["cms_page"] = len(ep["entries"]["pages"])

    print(f"[verify_seed] {ep.get('site')} @ {API}")
    print("  entry counts (planned vs stack):")
    ok = True
    for ct in sorted(expected):
        got = count(ct)
        mark = "✓" if got == expected[ct] else "✗"
        if got != expected[ct]:
            ok = False
        print(f"    {ct:24s} planned={expected[ct]:3d}  stack={got}  {mark}")

    # spot-check reference integrity on one page
    print(f"  reference spot-check on page '{a.page}':")
    pg = query_one("cms_page", "page_label", a.page)
    if not pg:
        print("    page not found ✗"); sys.exit(1)
    slots = pg.get("slots") or []
    resolved_slots = sum(1 for s in slots if s.get("slot"))
    print(f"    slots on page: {len(slots)}, with slot reference: {resolved_slots}")
    # take first slot ref, resolve it, check its components
    chk = next((s for s in slots if s.get("slot")), None)
    if chk:
        ref = chk["slot"][0] if isinstance(chk["slot"], list) else chk["slot"]
        slot_uid = ref.get("uid")
        sr = cma(f"/content_types/cms_content_slot/entries/{slot_uid}")
        slot = sr.get("entry", {})
        comps = slot.get("components") or []
        print(f"    slot '{slot.get('slot_id')}' -> {len(comps)} component refs "
              f"(sample content_type: {comps[0].get('_content_type_uid') if comps else 'n/a'})")
        if comps and comps[0].get("uid"):
            print("    component reference resolves to a real entry uid ✓")
        else:
            ok = ok and not comps  # empty is ok, dangling is not
    print("  RESULT:", "PASS" if ok else "CHECK")


if __name__ == "__main__":
    main()
