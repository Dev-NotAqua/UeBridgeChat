"""Build the Firefox package: `python build-firefox.py [--xpi]`.

Copies extension/ to dist/firefox/ with manifest-firefox.json as manifest.json.
Chrome's manifest.json uses background.service_worker (Firefox doesn't support
it); the Firefox manifest uses background.scripts (old Chrome refuses it) - so
one manifest can't serve both and this script keeps the two from drifting apart:
it fails loudly if versions, matches, scripts, permissions or icons differ.
Dev-only files (this manifest source, node tests) are left out of the package.

With --xpi it also zips the package to dist/ue-chat-bridge-<version>.xpi for
Firefox's "Install Add-on From File..." menu. Note: regular Firefox only
installs SIGNED .xpi files (submit to addons.mozilla.org for signing); for an
unsigned local build use Firefox Developer Edition / Nightly / ESR with
xpinstall.signatures.required=false, or Load Temporary Add-on (no signing
needed, but the id changes every browser restart).
Standard library only.
"""

import argparse
import json
import os
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "extension")
DST = os.path.join(HERE, "dist", "firefox")

# Keys that must be identical in both manifests (background + gecko differ by design).
SYNCED_KEYS = ("version", "name", "description", "icons", "action",
               "permissions", "host_permissions", "content_scripts")
SKIP_FILES = ("manifest.json", "manifest-firefox.json", "parse.test.js")


def load_manifest(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check_sync(chrome, firefox):
    problems = []
    for key in SYNCED_KEYS:
        if chrome.get(key) != firefox.get(key):
            problems.append(f"'{key}' differs between manifest.json and manifest-firefox.json")
    if not isinstance(firefox.get("background", {}).get("scripts"), list):
        problems.append("manifest-firefox.json needs background.scripts (Firefox has no service_worker)")
    if "service_worker" in firefox.get("background", {}):
        problems.append("manifest-firefox.json must not contain background.service_worker")
    if not firefox.get("browser_specific_settings", {}).get("gecko", {}).get("id"):
        problems.append("manifest-firefox.json needs browser_specific_settings.gecko.id")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the Firefox package.")
    parser.add_argument("--xpi", action="store_true",
                        help="also zip dist/firefox/ into a versioned .xpi for 'Install Add-on From File'")
    args = parser.parse_args(argv)
    chrome = load_manifest(os.path.join(SRC, "manifest.json"))
    firefox = load_manifest(os.path.join(SRC, "manifest-firefox.json"))
    problems = check_sync(chrome, firefox)
    if problems:
        print("Manifests out of sync - fix extension/manifest-firefox.json:")
        for p in problems:
            print(f"  - {p}")
        return 1
    if os.path.isdir(DST):
        shutil.rmtree(DST)
    for root, _, files in os.walk(SRC):
        for name in files:
            if name in SKIP_FILES:
                continue
            src = os.path.join(root, name)
            rel = os.path.relpath(src, SRC)
            dst = os.path.join(DST, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    shutil.copy2(os.path.join(SRC, "manifest-firefox.json"), os.path.join(DST, "manifest.json"))
    print(f"Firefox package ready: {DST}  (v{firefox['version']})")
    print("Load it in Firefox: about:debugging -> This Firefox -> Load Temporary Add-on")
    print("-> open dist/firefox/ and select manifest.json")
    if args.xpi:
        xpi = os.path.join(HERE, "dist", f"ue-chat-bridge-{firefox['version']}.xpi")
        with zipfile.ZipFile(xpi, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(DST):
                for name in files:
                    full = os.path.join(root, name)
                    z.write(full, os.path.relpath(full, DST))
        print(f"Signed-ready archive: {xpi}")
        print("(Regular Firefox installs only Mozilla-signed .xpi files - see README.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
