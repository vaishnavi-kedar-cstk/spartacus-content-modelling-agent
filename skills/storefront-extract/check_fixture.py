#!/usr/bin/env python3
"""Regression check for storefront-extract.

Re-extracts a site (primary language only — structural counts are language-independent)
and compares against the committed golden fixture. Reports NEW / MISSING component types
and any structural-count drift. A NEW type is a Gate-1 cue for content-model, not a hard
failure; count drift or MISSING types is a failure.

Usage: python3 check_fixture.py <site> [--base URL]
"""
import argparse, json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.normpath(os.path.join(HERE, "..", "..", "fixtures"))
COUNT_KEYS = ["contentPages", "layoutSamples", "uniqueSlots", "sharedSlots",
              "componentTypes", "componentInstances", "navigationNodes", "mediaAssets"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("site")
    ap.add_argument("--base", default="")
    a = ap.parse_args()

    fixture_path = os.path.join(FIXTURES, f"{a.site}.inventory.json")
    if not os.path.exists(fixture_path):
        sys.exit(f"no fixture for {a.site} at {fixture_path}")
    fixture = json.load(open(fixture_path))

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        tmp = tf.name
    cmd = [sys.executable, os.path.join(HERE, "extract.py"),
           "--site", a.site, "--primary-only", "--out", tmp, "--quiet"]
    if a.base:
        cmd += ["--base", a.base]
    subprocess.run(cmd, check=True)
    fresh = json.load(open(tmp))
    os.unlink(tmp)

    ok = True
    print(f"[check] {a.site}")
    # counts
    for k in COUNT_KEYS:
        fx, fr = fixture["counts"].get(k), fresh["counts"].get(k)
        flag = "" if fx == fr else "  <-- DRIFT"
        if fx != fr:
            ok = False
        print(f"  {k:20s} fixture={fx}  fresh={fr}{flag}")
    # component-type set
    fx_types = set(fixture["componentTypeInventory"])
    fr_types = set(fresh["componentTypeInventory"])
    new, missing = fr_types - fx_types, fx_types - fr_types
    if new:
        print("  NEW types (run Gate-1 in content-model):", sorted(new))
    if missing:
        ok = False
        print("  MISSING types (regression):", sorted(missing))
    if not new and not missing:
        print("  component-type set: unchanged")
    print("  RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
