#!/usr/bin/env python3
"""
orchestrate — the content-modeling AGENT: one governed command that runs the whole
pipeline for a storefront, with two human gates.

  extract ──▶ nav-resolve ──▶ model ──▶ [GATE 1: model approval]
          ──▶ [GATE 2: pre-publish] ──▶ seed ──▶ publish ──▶ verify

It sequences the skills (each a standalone, tested script), enforces the gates, and
handles the one judgment call the skills don't: an UNKNOWN typeCode (Gate 1). Everything
is idempotent — re-running updates in place — and resumable via the run directory.

GATES (fail-closed): the pipeline STOPS at each gate unless the matching approval flag is
passed, so nothing is created or published without an explicit human go.
  Gate 1  model approval   -> pass --approve-model    (only blocks if unknown typeCodes found)
  Gate 2  pre-publish      -> pass --approve-publish   (always blocks before any write)

Default (no approval flags) = a full DRY RUN: extract→model + both gate reports, no writes.

Usage:
  # dry run (safe): see the plan + gate reports, write nothing
  python3 orchestrate.py --site electronics-spa --source fixture

  # full governed run (requires CS_* env for seed/publish/verify)
  python3 orchestrate.py --site electronics-spa --source fixture \
      --approve-model --approve-publish --env development
"""
import argparse, json, os, subprocess, sys, datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.join(ROOT, "skills")
PROFILE = os.path.join(ROOT, "registry", "connector.registry.json")
FIXTURES = os.path.join(ROOT, "fixtures")
# The connector's shipped starter-pack content types. Self-contained default = the
# vendored copy in this repo; override with --starter-pack or CONNECTOR_STARTER_PACK to
# point at a live connector checkout instead.
_VENDORED = os.path.join(ROOT, "vendor", "starter-pack", "content_types")
_SIBLING = os.path.normpath(os.path.join(
    ROOT, "..", "contentstack-spartacus-feature", "import-export", "starter-pack", "content_types"))
DEFAULT_STARTER = os.environ.get("CONNECTOR_STARTER_PACK",
                                 _VENDORED if os.path.isdir(_VENDORED) else _SIBLING)
DEFAULT_OCC = os.environ.get("OCC_BASE_URL", "https://40.76.109.9:9002/occ/v2")

C = {"h": "\033[1m", "ok": "\033[32m", "warn": "\033[33m", "err": "\033[31m",
     "gate": "\033[35m", "z": "\033[0m"}


def banner(txt, color="h"):
    line = "─" * (len(txt) + 2)
    print(f"{C[color]}┌{line}┐\n│ {txt} │\n└{line}┘{C['z']}")


def step(txt):
    print(f"{C['h']}▶ {txt}{C['z']}")


def run(script, args, env=None):
    """Run a skill script; stream nothing, return (rc, output)."""
    p = subprocess.run([sys.executable, os.path.join(SKILLS, script), *args],
                       capture_output=True, text=True, env=env or os.environ.copy())
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out


def known_typecodes(profile):
    comp = {k for k in profile["component_map"] if not k.startswith("_")}
    occ = set(profile["occ_only_types"]["types"])
    return comp | occ


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--source", choices=["fixture", "live"], default="fixture")
    ap.add_argument("--base", default=DEFAULT_OCC, help="OCC v2 base URL (or set OCC_BASE_URL)")
    ap.add_argument("--starter-pack", default=DEFAULT_STARTER, help="connector starter-pack content_types dir")
    ap.add_argument("--env", default="development")
    ap.add_argument("--approve-model", action="store_true", help="pass Gate 1")
    ap.add_argument("--approve-publish", action="store_true", help="pass Gate 2 (enables writes)")
    ap.add_argument("--locales", default="", help="optional i18n: sapIso=csLocale,... e.g. de=de-de,ja=ja-jp,zh=zh-cn")
    ap.add_argument("--run-dir", default="")
    a = ap.parse_args()
    locale_map = dict(p.split("=", 1) for p in a.locales.split(",") if "=" in p) if a.locales else {}

    run_dir = a.run_dir or os.path.join("/tmp", f"orchestrate-{a.site}")
    os.makedirs(run_dir, exist_ok=True)
    inv_path = os.path.join(run_dir, "inventory.json")
    profile = json.load(open(PROFILE))
    manifest = {"site": a.site, "started": datetime.datetime.now().isoformat(timespec="seconds"),
                "stages": {}}

    def record(stage, status, note=""):
        manifest["stages"][stage] = {"status": status, "note": note}
        json.dump(manifest, open(os.path.join(run_dir, "manifest.json"), "w"), indent=2)

    banner(f"CONTENT-MODELING AGENT · {a.site} · profile connector-reference-fields")
    print(f"  run dir: {run_dir}   env: {a.env}   source: {a.source}")
    print(f"  occ: {a.base}   region: {os.environ.get('CS_REGION_BASE', 'https://api.contentstack.io')}")
    print(f"  approvals: model={'YES' if a.approve_model else 'no'} "
          f"publish={'YES' if a.approve_publish else 'no'}"
          f"{'   (DRY RUN — no writes)' if not a.approve_publish else ''}\n")

    # 1. EXTRACT (or load fixture)
    step("1/7 extract")
    if a.source == "fixture":
        fx = os.path.join(FIXTURES, f"{a.site}.inventory.json")
        if not os.path.exists(fx):
            record("extract", "error", "no fixture"); sys.exit(f"no fixture at {fx}")
        json.dump(json.load(open(fx)), open(inv_path, "w"))
        print(f"   loaded fixture -> {inv_path}")
    else:
        rc, out = run("storefront-extract/extract.py",
                      ["--site", a.site, "--base", a.base, "--out", inv_path, "--quiet"])
        print("   " + out.strip().splitlines()[-1] if out.strip() else "")
        if rc:
            record("extract", "error", out[-300:]); sys.exit("extract failed")
    record("extract", "ok")

    # 2. NAV RESOLVE
    step("2/7 nav-resolve")
    rc, out = run("storefront-extract/nav_resolve.py", ["--inventory", inv_path, "--site", a.site, "--base", a.base])
    print("   " + (out.strip().splitlines()[-1] if out.strip() else ""))
    record("nav-resolve", "ok" if not rc else "warn")

    # 3. MODEL
    step("3/7 model")
    rc, out = run("content-model/model_connector.py",
                  ["--inventory", inv_path, "--profile", PROFILE, "--out", run_dir])
    print("   " + "\n   ".join(out.strip().splitlines()))
    if rc:
        record("model", "error", out[-300:]); sys.exit("model failed")
    record("model", "ok")
    plan = json.load(open(os.path.join(run_dir, "connector-plan.json")))

    # 4. GATE 1 — model approval (unknown typeCodes)
    inv = json.load(open(inv_path))
    seen = set(inv.get("componentTypeInventory", {}))
    unknown = sorted(seen - known_typecodes(profile))
    print()
    banner("GATE 1 · MODEL APPROVAL", "gate")
    if unknown:
        print(f"  {C['warn']}UNKNOWN typeCodes (not in profile): {unknown}{C['z']}")
        print("  These need a human decision: add to component_map (authored) or occ_only_types (OCC).")
        if not a.approve_model:
            record("gate1", "blocked", f"unknown: {unknown}")
            print(f"\n  {C['err']}STOP{C['z']} — resolve the mapping, then re-run with --approve-model.")
            sys.exit(2)
        print(f"  {C['ok']}approved (--approve-model){C['z']} — proceeding.")
    else:
        print(f"  {C['ok']}no unknown typeCodes — all {len(seen)} classified.{C['z']}")
    record("gate1", "passed", f"unknown={unknown}")

    # 5. GATE 2 — pre-publish summary
    n_comp = len(plan["entries"]["components"])
    n_pages = len(plan["entries"]["pages"])
    print()
    banner("GATE 2 · PRE-PUBLISH", "gate")
    print(f"  will UPSERT to stack + publish to '{a.env}':")
    print(f"    content types : {len(plan['content_types_needed'])}")
    print(f"    assets        : {len(plan['assets'])}")
    print(f"    component entries: {n_comp}   pages: {n_pages}   global_slots: "
          f"{'1' if plan['entries'].get('global_slots') else '0'}")
    print(f"    pages: {[p['_key']['value'] for p in plan['entries']['pages']]}")
    if locale_map:
        print(f"    + localize into: {list(locale_map.values())} "
              f"(fully-localized OCC extract per language, incl. per-locale images)")
    if not a.approve_publish:
        record("gate2", "dry-run")
        print(f"\n  {C['warn']}DRY RUN complete{C['z']} — nothing written. "
              f"Re-run with --approve-publish (and CS_* env) to seed + publish.")
        sys.exit(0)
    if not (os.environ.get("CS_API_KEY") and os.environ.get("CS_MANAGEMENT_TOKEN")):
        record("gate2", "error", "no creds")
        sys.exit(f"{C['err']}--approve-publish set but CS_API_KEY / CS_MANAGEMENT_TOKEN missing.{C['z']}")
    print(f"  {C['ok']}approved (--approve-publish){C['z']} — writing.")
    record("gate2", "passed")

    plan_path = os.path.join(run_dir, "connector-plan.json")
    # 6. SEED
    step("4/7 seed (types + assets + entries)")
    rc, out = run("cs-seed/cma_seed_connector.py",
                  ["--plan", plan_path, "--starter-pack", a.starter_pack, "--stages", "types,assets,entries"])
    tail = "\n   ".join(out.strip().splitlines()[-6:])
    print("   " + tail)
    record("seed", "ok" if not rc else "error", out[-300:])
    if rc:
        sys.exit("seed failed")

    # 7. PUBLISH
    step("5/7 publish")
    rc, out = run("cs-seed/publish.py", ["--env", a.env, "--locale", "en-us"])
    print("   " + "\n   ".join(out.strip().splitlines()[-4:]))
    record("publish", "ok" if not rc else "error")

    # 8. VERIFY
    step("6/7 verify — round-trip")
    rc, out = run("verify/verify_connector.py", ["--inventory", inv_path, "--plan", plan_path])
    print("   " + "\n   ".join(out.strip().splitlines()[-3:]))
    rt_ok = rc == 0
    record("verify-roundtrip", "pass" if rt_ok else "check")

    step("7/7 verify — delivery read (landing page)")
    venv = os.environ.copy(); venv["CS_ENVIRONMENT"] = a.env
    rc, out = run("verify/cda_read.py", ["--content-type", "landing_page", "--url", "/"], env=venv)
    print("   " + "\n   ".join(out.strip().splitlines()[-2:]))
    record("verify-cda", "pass" if rc == 0 else "check")

    # 9. LOCALIZE (optional) — full localized OCC extract per language, then localize+publish.
    for sap_iso, cs_loc in locale_map.items():
        print()
        banner(f"LOCALIZE · {sap_iso} → {cs_loc}", "gate")
        linv = os.path.join(run_dir, f"inventory-{sap_iso}.json")
        lmodel = os.path.join(run_dir, f"model-{sap_iso}")
        # localized inventory (always live — i18n needs the localized OCC responses)
        rc, out = run("storefront-extract/extract.py",
                      ["--site", a.site, "--base", a.base, "--langs", sap_iso, "--primary-only", "--out", linv, "--quiet"])
        if rc:
            record(f"localize:{cs_loc}", "error", "extract failed"); continue
        run("storefront-extract/nav_resolve.py", ["--inventory", linv, "--site", a.site, "--base", a.base, "--lang", sap_iso])
        rc, out = run("content-model/model_connector.py", ["--inventory", linv, "--profile", PROFILE, "--out", lmodel])
        if rc:
            record(f"localize:{cs_loc}", "error", "model failed"); continue
        rc, out = run("cs-seed/localize.py",
                      ["--locale", cs_loc, "--plan", os.path.join(lmodel, "connector-plan.json"), "--env", a.env])
        print("   " + "\n   ".join(out.strip().splitlines()[-4:]))
        record(f"localize:{cs_loc}", "ok" if not rc else "error", out[-200:])

    print()
    banner(f"DONE · {a.site} · round-trip {'PASS' if rt_ok else 'CHECK'}"
           f"{' · +' + str(len(locale_map)) + ' locales' if locale_map else ''}",
           "ok" if rt_ok else "warn")
    print(f"  manifest: {run_dir}/manifest.json")


if __name__ == "__main__":
    main()
