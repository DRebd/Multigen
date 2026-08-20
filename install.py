#!/usr/bin/env python3
"""Installer for the imagegen Claude Code skill.

Stdlib-only and cross-platform (PowerShell, cmd, Git Bash, Linux/macOS):

    python install.py              install/update the skill
    python install.py --uninstall  remove the skill (config and outputs kept)

Contract (PDR sections 15.2 / 16.2):
  * idempotent — safe to re-run after git pull
  * copies skill/ -> ~/.claude/skills/imagegen/ (existing install is moved to
    ~/.claude/skills/imagegen.bak.<timestamp> first)
  * creates ~/.config/imagegen/env from the template ONLY if absent; never
    overwrites, reads back, or echoes key values; chmod 600 best-effort
  * creates the default output directory
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
REPO_SKILL = Path(__file__).resolve().parent / "skill"
DEST = HOME / ".claude" / "skills" / "imagegen"
CONFIG_DIR = HOME / ".config" / "imagegen"
CONFIG_FILE = CONFIG_DIR / "env"
OUTPUT_DIR = HOME / "Pictures" / "imagegen"

CONFIG_TEMPLATE = """\
# imagegen configuration — plain KEY=VALUE lines, '#' comments allowed.
# Uncomment and fill in the providers you want. Any one key enables its
# provider; unset providers are silently skipped by the router.

# --- Tier 1: reliable mainstream ---
#GEMINI_API_KEY=
#OPENAI_API_KEY=

# --- Tier 2: permissive frontier ---
#XAI_API_KEY=

# --- Tier 3: local ComfyUI (free, private; URL presence enables it) ---
#COMFYUI_URL=http://127.0.0.1:8188
#COMFYUI_CHECKPOINT=sd_xl_base_1.0.safetensors   # empty = first on server
#COMFYUI_STEPS=25
#COMFYUI_CFG=7.0
#COMFYUI_SAMPLER=euler
#COMFYUI_SCHEDULER=normal
#COMFYUI_NEGATIVE=blurry, lowres, jpeg artifacts, deformed, watermark, text
#COMFYUI_PREFIX=         # auto-prepended to every comfy prompt (Pony: score tags)
#COMFYUI_CLIP_SKIP=1     # 1 for SDXL/realistic; 2 for Pony/Illustrious/NoobAI
#COMFYUI_VAE=            # empty = checkpoint's baked VAE; else a VAE filename
#COMFYUI_TIMEOUT=600
# Pony Diffusion V6 XL profile (uncomment as a set):
#COMFYUI_CHECKPOINT=ponyDiffusionV6XL_v6StartWithThisOne.safetensors
#COMFYUI_VAE=sdxl_vae.safetensors
#COMFYUI_CLIP_SKIP=2
#COMFYUI_PREFIX=score_9, score_8_up, score_7_up
#COMFYUI_NEGATIVE=score_6, score_5, score_4, worst quality, low quality, blurry, watermark, text, signature

# Civitai model downloads (tools/civitai_fetch.py). Make a token at
# civitai.com -> Account -> API Keys; NSFW models also need mature content
# enabled on the account. Used only by the fetch tool, never by the router.
#CIVITAI_API_TOKEN=

# --- Preferences ---
#IMAGEGEN_OUTPUT_DIR=~/Pictures/imagegen
#IMAGEGEN_TIER_ORDER=gemini,openai,grok,comfy
#IMAGEGEN_FALLTHROUGH_CONTENT=1
#IMAGEGEN_TIMEOUT=120

# --- Phase 2 (recognized but inert until the upscale feature ships) ---
#IMAGEGEN_UPSCALE=0      # Phase 2
"""


def chmod_600(path: Path) -> None:
    """Best-effort: apply on POSIX, silently skip on Windows/NTFS."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def install() -> int:
    if not REPO_SKILL.is_dir():
        print(f"error: {REPO_SKILL} not found (run from the repo checkout)", file=sys.stderr)
        return 1
    if DEST.exists():
        # Backups must live OUTSIDE ~/.claude/skills/ — Claude Code loads every
        # directory there as a skill, so an in-place .bak becomes a duplicate.
        backup_root = HOME / ".claude" / "imagegen-backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        backup = backup_root / f"imagegen.bak.{datetime.now().strftime('%Y%m%d%H%M%S')}"
        shutil.move(str(DEST), str(backup))
        print(f"existing install moved to {backup}")
    DEST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(REPO_SKILL), str(DEST))
    print(f"installed skill -> {DEST}")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        print(f"created config template -> {CONFIG_FILE}")
    else:
        print(f"kept existing config -> {CONFIG_FILE}")
    chmod_600(CONFIG_FILE)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"output directory ready -> {OUTPUT_DIR}")

    print()
    print("Next steps:")
    print(f"  1. Edit {CONFIG_FILE} and add at least one API key")
    print("     (GEMINI_API_KEY, OPENAI_API_KEY, or XAI_API_KEY).")
    print("  2. Check configuration (use python / py -3 / python3, whichever works):")
    print(f"     python {DEST / 'scripts' / 'imagegen.py'} status")
    print("  3. Live smoke tests for the providers you configured:")
    script = DEST / "scripts" / "imagegen.py"
    for p in ("gemini", "openai", "grok"):
        print(f'     python {script} generate "a red cube on a white background" --provider {p} --json')
    return 0


def uninstall() -> int:
    if DEST.exists():
        shutil.rmtree(str(DEST))
        print(f"removed {DEST}")
    else:
        print(f"nothing to remove at {DEST}")
    print(f"kept config ({CONFIG_FILE}) and outputs ({OUTPUT_DIR})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the imagegen skill")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    return uninstall() if args.uninstall else install()


if __name__ == "__main__":
    raise SystemExit(main())
