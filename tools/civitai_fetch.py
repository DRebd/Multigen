#!/usr/bin/env python3
"""civitai_fetch — download a Civitai model into a ComfyUI install.

Stdlib-only, cross-platform (PowerShell / Git Bash / Linux). Resolves a
Civitai *model-version* via the public API, then downloads its primary file
into the correct ComfyUI subfolder by type (Checkpoint -> checkpoints,
LORA -> loras, VAE -> vae, ...). Resumable, with progress and an optional
SHA256 verify.

    python civitai_fetch.py 128713
    python civitai_fetch.py https://civitai.com/models/257749?modelVersionId=290640
    python civitai_fetch.py 290640 --comfy C:\\Users\\DRR\\ComfyUI --verify

Auth: many models (and all NSFW ones) require a token. Create one at
civitai.com -> Account -> API Keys, then put it in the environment as
CIVITAI_API_TOKEN (this tool reads it; it never prints it). The token is
sent as an Authorization: Bearer header, never placed in the saved path or
logged.

Guardrail: refuses models flagged poi=true (real-person likeness) unless
--allow-poi is passed. This tool is for fictional/original content; do not
use it to build likenesses of real people.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_VERSION = "https://civitai.com/api/v1/model-versions/{id}"
DOWNLOAD = "https://civitai.com/api/download/models/{id}"

# Civitai file "type" -> ComfyUI models/<subdir>
TYPE_DIR = {
    "Model": "checkpoints",          # a checkpoint file's type is "Model"
    "Checkpoint": "checkpoints",
    "Pruned Model": "checkpoints",
    "VAE": "vae",
    "LORA": "loras",
    "LoCon": "loras",
    "DoRA": "loras",
    "Controlnet": "controlnet",
    "Upscaler": "upscale_models",
    "Embeddings": "embeddings",
    "Textual Inversion": "embeddings",
}


def _token() -> str:
    return os.environ.get("CIVITAI_API_TOKEN", "").strip()


# Civitai sits behind Cloudflare, which 403s the default Python-urllib UA.
USER_AGENT = "civitai_fetch/1.0 (+imagegen; stdlib urllib)"


def _headers() -> dict:
    h = {"User-Agent": USER_AGENT}
    tok = _token()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def parse_version_id(arg: str):
    """Accept a bare version id, or a Civitai URL with ?modelVersionId=."""
    s = arg.strip()
    if s.isdigit():
        return int(s)
    m = re.search(r"[?&]modelVersionId=(\d+)", s)
    if m:
        return int(m.group(1))
    m = re.search(r"/model-versions/(\d+)", s)
    if m:
        return int(m.group(1))
    return None


def get_version(version_id: int) -> dict:
    url = API_VERSION.format(id=version_id)
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except OSError:
            pass
        raise SystemExit(f"error: Civitai API HTTP {e.code} for version "
                         f"{version_id}: {detail or e.reason}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SystemExit(f"error: could not reach Civitai API: {e}")


def pick_file(version: dict) -> dict:
    files = version.get("files") or []
    if not files:
        raise SystemExit("error: this model version has no downloadable files")
    for f in files:
        if f.get("primary"):
            return f
    # otherwise the largest .safetensors, else the largest file
    st = [f for f in files if str(f.get("name", "")).endswith(".safetensors")]
    pool = st or files
    return max(pool, key=lambda f: f.get("sizeKB") or 0)


def pick_files(version: dict, want_all: bool) -> list:
    """Primary file only, or every file in the version (checkpoint + its VAE,
    config, etc.) — each routed to its own ComfyUI subdir by type."""
    if not want_all:
        return [pick_file(version)]
    files = version.get("files") or []
    if not files:
        raise SystemExit("error: this model version has no downloadable files")
    primary = pick_file(version)
    rest = [f for f in files if f is not primary]
    return [primary] + rest


def human(kb) -> str:
    if not kb:
        return "?"
    mb = kb / 1024
    return f"{mb/1024:.2f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def download(url: str, dest: Path, expect_bytes: int | None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    have = tmp.stat().st_size if tmp.exists() else 0
    headers = dict(_headers())
    if have:
        headers["Range"] = f"bytes={have}-"
        print(f"resuming at {have/1048576:.0f} MB")
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            mode = "ab" if (have and resp.status == 206) else "wb"
            if mode == "wb":
                have = 0
            total = expect_bytes
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit():
                total = have + int(cl)
            written = have
            last = -1
            with open(tmp, mode) as fh:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
                    written += len(chunk)
                    if total:
                        pct = int(written * 100 / total)
                        if pct != last:
                            last = pct
                            bar = "#" * (pct // 3)
                            sys.stdout.write(
                                f"\r  {pct:3d}% |{bar:<34}| "
                                f"{written/1048576:6.0f} MB")
                            sys.stdout.flush()
            sys.stdout.write("\n")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SystemExit(
                f"error: HTTP {e.code} — this model needs a valid token. "
                "Set CIVITAI_API_TOKEN (Account -> API Keys) and, for NSFW "
                "models, enable mature content on your Civitai account.")
        raise SystemExit(f"error: download HTTP {e.code}: {e.reason}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SystemExit(f"error: download failed (rerun to resume): {e}")
    tmp.replace(dest)


def verify_sha256(path: Path, expected: str) -> bool:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest().lower() == expected.lower()


def main() -> int:
    ap = argparse.ArgumentParser(description="Download a Civitai model into ComfyUI")
    ap.add_argument("target", help="model-version id, or a Civitai URL with modelVersionId")
    ap.add_argument("--comfy", default=os.environ.get("COMFYUI_HOME",
                    str(Path(os.path.expanduser("~")) / "ComfyUI")),
                    help="ComfyUI install dir (default ~/ComfyUI or $COMFYUI_HOME)")
    ap.add_argument("--dir", help="override the models/<subdir> destination name")
    ap.add_argument("--verify", action="store_true", help="SHA256-verify after download")
    ap.add_argument("--all", action="store_true", dest="all_files",
                    help="download every file in the version (e.g. a checkpoint "
                         "plus its companion VAE), each routed by type")
    ap.add_argument("--allow-poi", action="store_true",
                    help="permit real-person (poi) models — off by default")
    args = ap.parse_args()

    vid = parse_version_id(args.target)
    if vid is None:
        raise SystemExit("error: could not find a model-version id in "
                         f"'{args.target}' (pass the number, or a URL with "
                         "?modelVersionId=)")

    version = get_version(vid)
    model = version.get("model") or {}
    base = version.get("baseModel") or "?"
    mtype = model.get("type") or "?"
    print(f"model:     {model.get('name', '?')}  [{mtype}, base {base}]")
    print(f"version:   {version.get('name', '?')}  (id {vid})")
    trained = version.get("trainedWords") or []
    if trained:
        print(f"triggers:  {', '.join(trained[:12])}")

    if model.get("poi") and not args.allow_poi:
        raise SystemExit(
            "refused: this model is flagged poi=true (depicts a real person). "
            "This tool is for fictional/original content. Re-run with "
            "--allow-poi only if you are certain this is not a likeness of a "
            "real individual.")

    if base.lower().startswith(("pony", "illustrious", "noob")):
        print("note:      this base expects CLIP skip 2 — set COMFYUI_CLIP_SKIP=2")
    if not _token():
        print("note:      CIVITAI_API_TOKEN not set — ungated models still "
              "download; gated/NSFW ones need a token.")

    targets = pick_files(version, args.all_files)
    if not args.all_files and len(version.get("files") or []) > 1:
        extras = len(version["files"]) - 1
        print(f"note:      {extras} other file(s) in this version "
              "(pass --all to fetch them too, e.g. a companion VAE)")

    saved = []
    for f in targets:
        size_kb = f.get("sizeKB")
        subdir = args.dir or TYPE_DIR.get(f.get("type") or mtype, "checkpoints")
        dest = Path(args.comfy) / "models" / subdir / f["name"]
        print(f"\nfile:      {f['name']}  ({human(size_kb)})  -> models/{subdir}/")
        if dest.exists():
            print("           already present; skipping download.")
        else:
            url = f.get("downloadUrl") or DOWNLOAD.format(id=vid)
            download(url, dest, int(size_kb * 1024) if size_kb else None)
            print(f"           saved: {dest}")
        if args.verify:
            want = ((f.get("hashes") or {}).get("SHA256") or "").strip()
            if not want:
                print("           verify: no SHA256 published; skipped")
            elif verify_sha256(dest, want):
                print("           verify: SHA256 OK")
            else:
                raise SystemExit(f"verify: SHA256 MISMATCH on {f['name']} — "
                                 "delete it and re-download")
        saved.append((f, subdir))

    ckpts = [f for f, sd in saved if sd == "checkpoints"]
    vaes = [f for f, sd in saved if sd == "vae"]
    print("\nNext, in ~/.config/imagegen/env:")
    if ckpts:
        print(f"  COMFYUI_CHECKPOINT={ckpts[0]['name']}")
    if vaes:
        print(f"  COMFYUI_VAE={vaes[0]['name']}")
    if base.lower().startswith(("pony", "illustrious", "noob")):
        print("  COMFYUI_CLIP_SKIP=2")
    print("then generate with --provider comfy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
