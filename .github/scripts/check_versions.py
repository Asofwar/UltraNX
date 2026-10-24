#!/usr/bin/env python3
"""Compare component versions bundled in the SD tree with the latest upstream releases.

Usage: check_versions.py [--root DIR] [--offline]
Writes a markdown table to stdout (and to $GITHUB_STEP_SUMMARY when set).
Exit code is always 0 unless the manifest itself is broken; outdated components are reported, not failed.
"""
import argparse
import json
import os
import pathlib
import re
import struct
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
MANIFEST = HERE.parent / "components.json"


def nacp_version(data: bytes) -> str:
    """Version string from the NACP of an .nro/.ovl (homebrew asset section)."""
    if data[0x10:0x14] != b"NRO0":
        raise ValueError("not an NRO")
    size = struct.unpack_from("<I", data, 0x18)[0]
    asset = data[size:]
    if asset[:4] != b"ASET":
        raise ValueError("no asset section")
    off, sz = struct.unpack_from("<QQ", asset, 0x18)
    nacp = asset[off:off + sz]
    return nacp[0x3060:0x3070].split(b"\0")[0].decode(errors="replace").strip()


def magic_version(data: bytes, magic: str) -> str:
    """hekate-style payload header: 4-byte magic + 3 ASCII digits at 0x118."""
    head = data[0x118:0x11F]
    if head[:4] != magic.encode():
        raise ValueError(f"magic {magic} not found")
    return ".".join(chr(c) for c in head[4:7])


def local_version(root: pathlib.Path, comp: dict) -> str:
    kind = comp["kind"]
    if kind == "pinned":
        return comp["version"]
    data = (root / comp["path"]).read_bytes()
    if kind == "nacp":
        return nacp_version(data)
    if kind == "toolbox":
        return str(json.loads(data).get("version") or "?")
    if kind == "magic":
        return magic_version(data, comp["magic"])
    if kind == "regex":
        m = re.search(comp["regex"].encode(), data)
        if not m:
            raise ValueError("version pattern not found")
        return m.group(1).decode()
    raise ValueError(f"unknown kind {kind}")


def latest_release(repo: str) -> str:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases?per_page=10",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "ultranx-ci"},
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        releases = json.load(r)
    stable = [x for x in releases if not x.get("prerelease") and not x.get("draft")]
    return (stable or releases)[0]["tag_name"] if releases else "?"


def norm(v: str) -> tuple:
    """Numeric tuple for comparison; '2.6.0-rel', 'v1.4.1+r4' -> (2,6,0), (1,4,1,4)."""
    return tuple(int(x) for x in re.findall(r"\d+", v))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(HERE.parent.parent))
    ap.add_argument("--offline", action="store_true", help="only read local versions")
    args = ap.parse_args()
    root = pathlib.Path(args.root)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    rows = []
    comps = [{"name": "Atmosphère", "repo": manifest["atmosphere"]["repo"], "kind": "pinned",
              "version": manifest["atmosphere"]["tag"]}] + manifest["components"]
    outdated = 0
    for comp in comps:
        try:
            local = local_version(root, comp)
        except Exception as e:  # report and keep going
            local = f"ERR: {e}"
        latest = "-"
        status = ""
        if not args.offline:
            try:
                latest = latest_release(comp["repo"])
                if local.startswith("ERR"):
                    status = "?"
                elif norm(local)[:3] < norm(latest)[:3]:
                    status = "outdated"
                    outdated += 1
                else:
                    status = "ok"
            except Exception as e:
                latest, status = f"ERR: {e}", "?"
        rows.append((comp["name"], f"[{comp['repo']}](https://github.com/{comp['repo']}/releases)", local, latest, status))

    out = ["| Component | Upstream | Bundled | Latest | Status |", "|---|---|---|---|---|"]
    out += [f"| {' | '.join(r)} |" for r in rows]
    if not args.offline:
        out.append("")
        out.append(f"**{outdated} outdated**" if outdated else "**All components up to date**")
    text = "\n".join(out) + "\n"
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## Component versions\n\n" + text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
