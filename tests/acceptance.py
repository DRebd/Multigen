#!/usr/bin/env python3
"""Offline acceptance suite for the imagegen skill (PDR section 10).

Runs A1-A3 and A6-A11 with zero network access and no real credentials:
routing and failure logic are proven via the hidden IMAGEGEN_MOCK hook
(A7's fallback uses the mock's OK directive instead of a live call; the
live smokes A4/A5 are deferred to the local machine, PDR section 15.4).

Stdlib-only and cross-platform; canonical runner for both the Linux cloud
sandbox and the Windows target. Exit 0 = all green.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "skill" / "scripts" / "imagegen.py"

PASS, FAIL = 0, 0
FAILURES = []


def check(test_id: str, desc: str, cond: bool, extra: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {test_id}: {desc}")
    else:
        FAIL += 1
        FAILURES.append(f"{test_id}: {desc} {extra}")
        print(f"  FAIL {test_id}: {desc} {extra}")


def run(args, env_extra=None, home=None):
    """Run imagegen.py in an isolated HOME; returns (code, stdout, stderr)."""
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("IMAGEGEN") or k.startswith("COMFYUI")
                   or k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY"))}
    if home:
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)  # Windows expanduser
        env["IMAGEGEN_OUTPUT_DIR"] = str(Path(home) / "out")
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, env=env, timeout=120)
    return proc.returncode, proc.stdout, proc.stderr


def last_json(stdout: str):
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except ValueError:
        return None


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="imagegen_test_"))
    dummy = {"GEMINI_API_KEY": "dummy-gem-key-123",
             "OPENAI_API_KEY": "dummy-oai-key-123",
             "XAI_API_KEY": "dummy-xai-key-123"}

    # --- A1: status --json with no config -----------------------------------
    code, out, _ = run(["status", "--json"], home=tmp)
    doc = last_json(out)
    check("A1", "exit 3 with no config", code == 3, f"(got {code})")
    check("A1", "providers listed as not_configured with env var names",
          doc is not None
          and all(doc["providers"][p]["state"] == "not_configured"
                  and doc["providers"][p]["set_env_var"]
                  for p in ("gemini", "openai", "grok")))
    check("A1", "comfy shown as not_configured with COMFYUI_URL hint",
          doc is not None and doc["providers"]["comfy"]["state"] == "not_configured"
          and doc["providers"]["comfy"]["set_env_var"] == "COMFYUI_URL")

    # --- A2: status --json with keys set ------------------------------------
    code, out, _ = run(["status", "--json"],
                       env_extra={**dummy, "COMFYUI_URL": "http://203.0.113.9:8188"},
                       home=tmp)
    doc = last_json(out)
    check("A2", "exit 0 with keys set", code == 0, f"(got {code})")
    check("A2", "providers configured (documented unprobed)",
          doc is not None
          and all(doc["providers"][p]["state"] == "configured"
                  and doc["providers"][p]["probe"] == "unprobed"
                  for p in ("gemini", "openai", "grok")))
    check("A2", "keys redacted, raw values never printed",
          "***set***" in out and "dummy-gem-key-123" not in out
          and "dummy-oai-key-123" not in out and "dummy-xai-key-123" not in out)
    check("A2", "COMFYUI_URL marks comfy configured with url shown",
          doc is not None and doc["providers"]["comfy"]["state"] == "configured"
          and doc["providers"]["comfy"].get("url"))
    check("A2", "public plain-http COMFYUI_URL triggers exposure warning",
          doc is not None and any("COMFYUI_URL" in w for w in doc.get("warnings", [])))

    # --- A3: dry run, offline, redacted -------------------------------------
    code, out, _ = run(["generate", "test", "--dry-run", "--json"],
                       env_extra=dummy, home=tmp)
    doc = last_json(out)
    check("A3", "dry-run exits 0", code == 0, f"(got {code})")
    check("A3", "per-provider requests printed for all four providers",
          doc is not None
          and {r["provider"] for r in doc.get("requests", [])}
          == {"gemini", "openai", "grok", "comfy"})
    check("A3", "auth redacted in dry-run output",
          "Bearer ***" in out and "***" in out
          and not any(v in out for v in dummy.values()))

    # --- A6: tier-3 comfy surfaces (Phase 2 shipped) --------------------------
    code, out, _ = run(["generate", "x", "--provider", "comfy", "--json"], home=tmp)
    doc = last_json(out)
    check("A6", "--provider comfy without COMFYUI_URL exits 3 NOT_CONFIGURED",
          code == 3 and doc and doc.get("error_class") == "NOT_CONFIGURED"
          and "COMFYUI_URL" in doc.get("detail", ""))
    code, out, _ = run(["generate", "x", "--tier", "3", "--json"], home=tmp)
    doc = last_json(out)
    check("A6", "--tier 3 without COMFYUI_URL exits 3 NOT_CONFIGURED",
          code == 3 and doc and doc.get("error_class") == "NOT_CONFIGURED")
    code, out, _ = run(["models"], home=tmp)
    doc = last_json(out)
    check("A6", "models without COMFYUI_URL exits 3 NOT_CONFIGURED",
          code == 3 and doc and doc.get("error_class") == "NOT_CONFIGURED")
    code, out, _ = run(["generate", "x", "--json"],
                       env_extra={**dummy, "IMAGEGEN_MOCK": "gemini:OK",
                                  "IMAGEGEN_TIER_ORDER": "comfy,gemini,openai,grok"},
                       home=tmp)
    doc = last_json(out)
    check("A6", "unconfigured comfy in IMAGEGEN_TIER_ORDER skipped, next provider serves",
          code == 0 and doc and doc.get("provider") == "gemini"
          and doc.get("attempts")
          and doc["attempts"][0]["provider"] == "comfy"
          and doc["attempts"][0]["error_class"] == "NOT_CONFIGURED")
    code, out, _ = run(["generate", "x", "--provider", "comfy", "--json"],
                       env_extra={"COMFYUI_URL": "http://127.0.0.1:8188",
                                  "IMAGEGEN_MOCK": "comfy:OK"},
                       home=tmp)
    doc = last_json(out)
    check("A6", "configured comfy serves at tier 3 with zero cost (mocked)",
          code == 0 and doc and doc.get("provider") == "comfy"
          and doc.get("tier") == 3 and doc.get("est_cost_usd") == 0.0)

    # --- A7 (offline variant): fallback path via mock ------------------------
    # Live A4/A5/A7 with real keys are deferred to the local machine; here the
    # mock hook proves the routing: gemini fails AUTH, openai serves.
    code, out, _ = run(["generate", "fallback test", "--json"],
                       env_extra={**dummy, "IMAGEGEN_MOCK": "gemini:AUTH,openai:OK"},
                       home=tmp)
    doc = last_json(out)
    check("A7", "auto mode falls through AUTH failure to next provider",
          code == 0 and doc and doc.get("ok") is True and doc.get("provider") == "openai")
    check("A7", "attempts[0].error_class == AUTH",
          doc is not None and doc.get("attempts")
          and doc["attempts"][0]["provider"] == "gemini"
          and doc["attempts"][0]["error_class"] == "AUTH")
    check("A7", "PNG written to reported path",
          doc is not None and doc.get("images")
          and all(Path(p).is_file() and Path(p).stat().st_size > 0
                  for p in doc["images"]))

    # --- A8: content fallthrough config --------------------------------------
    code, out, _ = run(["generate", "x", "--json"],
                       env_extra={**dummy,
                                  "IMAGEGEN_MOCK": "gemini:CONTENT_REJECTED",
                                  "IMAGEGEN_FALLTHROUGH_CONTENT": "0"},
                       home=tmp)
    doc = last_json(out)
    check("A8", "FALLTHROUGH=0 stops on CONTENT_REJECTED with exit 5",
          code == 5 and doc and doc.get("error_class") == "CONTENT_REJECTED")
    code, out, _ = run(["generate", "x", "--json"],
                       env_extra={**dummy,
                                  "IMAGEGEN_MOCK": "gemini:CONTENT_REJECTED,openai:OK",
                                  "IMAGEGEN_FALLTHROUGH_CONTENT": "1"},
                       home=tmp)
    doc = last_json(out)
    check("A8", "FALLTHROUGH=1 proceeds to next provider",
          code == 0 and doc and doc.get("provider") == "openai"
          and doc["attempts"][0]["error_class"] == "CONTENT_REJECTED")

    # --- A9: JSON contract on every terminal state ---------------------------
    terminal_cases = [
        (["status", "--json"], {}),
        (["status"], dummy),
        (["generate", "x", "--json"], {}),                          # exit 3
        (["generate", "x"], {**dummy, "IMAGEGEN_MOCK":
                             "gemini:PROVIDER_ERROR,openai:PROVIDER_ERROR,grok:OK"}),
        (["generate", "x", "--provider", "comfy"], {}),             # exit 3
        (["generate", "x", "--badflag"], {}),                       # argparse usage
        (["models"], {}),
        (["generate", "x", "--json"],
         {**dummy, "IMAGEGEN_MOCK": "gemini:AUTH,openai:AUTH,grok:AUTH"}),  # exit 4
        (["generate", "x", "--size", "banana", "--json"], dummy),   # exit 2
    ]
    for i, (case_args, env_extra) in enumerate(terminal_cases):
        code, out, _ = run(case_args, env_extra=env_extra, home=tmp)
        check("A9", f"case {i} ({' '.join(case_args)}) final stdout line is valid JSON",
              last_json(out) is not None, f"(exit {code})")

    # exit-code spot checks for the taxonomy
    code, _, _ = run(["generate", "x", "--json"],
                     env_extra={**dummy, "IMAGEGEN_MOCK": "gemini:AUTH,openai:AUTH,grok:AUTH"},
                     home=tmp)
    check("A9", "all non-content failures exit 4", code == 4, f"(got {code})")
    code, _, _ = run(["generate", "x", "--json"],
                     env_extra={**dummy, "IMAGEGEN_MOCK":
                                "gemini:CONTENT_REJECTED,openai:CONTENT_REJECTED,grok:CONTENT_REJECTED"},
                     home=tmp)
    check("A9", "all-content-rejected exits 5", code == 5, f"(got {code})")

    # --- A10: stdlib-only imports --------------------------------------------
    import ast
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
    non_stdlib = modules - set(sys.stdlib_module_names)
    check("A10", "imagegen.py imports stdlib only", not non_stdlib, f"({non_stdlib})")

    # --- A11: provenance ------------------------------------------------------
    code, out, _ = run(["generate", "enhanced prompt here", "--source", "orig", "--json"],
                       env_extra={**dummy, "IMAGEGEN_MOCK": "gemini:OK"}, home=tmp)
    doc = last_json(out)
    check("A11", "--source recorded as source_prompt",
          code == 0 and doc and doc.get("source_prompt") == "orig"
          and doc.get("prompt") == "enhanced prompt here")
    code, out, _ = run(["generate", "x", "--json"],
                       env_extra={**dummy, "IMAGEGEN_MOCK": "gemini:OK"}, home=tmp)
    doc = last_json(out)
    check("A11", "omitted --source yields source_prompt null",
          code == 0 and doc and doc.get("source_prompt") is None)

    # --- A12: --pre prompt blocks ---------------------------------------------
    pre_file = tmp / "style_block.txt"
    pre_file.write_text("ornate fresco style\n", encoding="utf-8")
    code, out, _ = run(["generate", "scene text", "--pre", str(pre_file), "--json"],
                       env_extra={**dummy, "IMAGEGEN_MOCK": "gemini:OK"}, home=tmp)
    doc = last_json(out)
    check("A12", "--pre prepends file content to the prompt with a note",
          code == 0 and doc
          and doc.get("prompt") == "ornate fresco style\nscene text"
          and any("--pre" in n for n in doc.get("notes", [])))
    code, out, _ = run(["generate", "x", "--pre", str(tmp / "missing.txt"), "--json"],
                       env_extra=dummy, home=tmp)
    doc = last_json(out)
    check("A12", "missing --pre file exits 2 USAGE",
          code == 2 and doc and doc.get("error_class") == "USAGE")

    # --- A13: comfy workflow tuning (clip skip + external VAE) ----------------
    code, out, _ = run(["generate", "x", "--provider", "comfy", "--dry-run", "--json"],
                       env_extra={"COMFYUI_URL": "http://127.0.0.1:8188",
                                  "COMFYUI_CLIP_SKIP": "2",
                                  "COMFYUI_VAE": "sdxl_vae.safetensors"},
                       home=tmp)
    doc = last_json(out)
    graph = None
    if doc and doc.get("requests"):
        graph = doc["requests"][0]["body"]["prompt"]
    check("A13", "CLIP skip 2 inserts CLIPSetLastLayer at stop_at_clip_layer -2",
          graph is not None
          and graph.get("10", {}).get("class_type") == "CLIPSetLastLayer"
          and graph["10"]["inputs"]["stop_at_clip_layer"] == -2
          and graph["6"]["inputs"]["clip"] == ["10", 0])
    check("A13", "COMFYUI_VAE routes VAEDecode through a VAELoader node",
          graph is not None
          and graph.get("11", {}).get("class_type") == "VAELoader"
          and graph["11"]["inputs"]["vae_name"] == "sdxl_vae.safetensors"
          and graph["8"]["inputs"]["vae"] == ["11", 0])
    code, out, _ = run(["generate", "x", "--provider", "comfy", "--dry-run", "--json"],
                       env_extra={"COMFYUI_URL": "http://127.0.0.1:8188"}, home=tmp)
    doc = last_json(out)
    graph = doc["requests"][0]["body"]["prompt"] if doc and doc.get("requests") else None
    check("A13", "defaults: clip skip 1 (-1) and baked VAE (no VAELoader)",
          graph is not None
          and graph["10"]["inputs"]["stop_at_clip_layer"] == -1
          and "11" not in graph
          and graph["8"]["inputs"]["vae"] == ["4", 2])

    # --- A14: civitai_fetch helper is stdlib-only and parses ids --------------
    import ast as _ast
    fetch = REPO / "tools" / "civitai_fetch.py"
    ftree = _ast.parse(fetch.read_text(encoding="utf-8"))
    fmods = set()
    for node in _ast.walk(ftree):
        if isinstance(node, _ast.Import):
            fmods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, _ast.ImportFrom) and node.module and node.level == 0:
            fmods.add(node.module.split(".")[0])
    check("A14", "civitai_fetch.py imports stdlib only",
          not (fmods - set(sys.stdlib_module_names)), f"({fmods - set(sys.stdlib_module_names)})")
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location("civitai_fetch", fetch)
    cf = _ilu.module_from_spec(spec); spec.loader.exec_module(cf)
    check("A14", "parse_version_id handles bare id and URL forms",
          cf.parse_version_id("290640") == 290640
          and cf.parse_version_id("https://civitai.com/models/257749?modelVersionId=290640") == 290640
          and cf.parse_version_id("not-an-id") is None)
    check("A14", "poi/type maps and UA header present",
          cf.TYPE_DIR.get("LORA") == "loras" and cf.TYPE_DIR.get("VAE") == "vae"
          and "User-Agent" in cf._headers())
    fake_version = {"files": [
        {"name": "ckpt.safetensors", "type": "Model", "primary": True, "sizeKB": 100},
        {"name": "companion_vae.safetensors", "type": "VAE", "sizeKB": 10},
    ]}
    check("A14", "--all returns every file, primary first; default returns one",
          [f["name"] for f in cf.pick_files(fake_version, False)] == ["ckpt.safetensors"]
          and [f["name"] for f in cf.pick_files(fake_version, True)]
          == ["ckpt.safetensors", "companion_vae.safetensors"])

    # --- A15: COMFYUI_PREFIX (checkpoint-family prompt lead-in) ---------------
    code, out, _ = run(["generate", "a knight", "--provider", "comfy", "--dry-run", "--json"],
                       env_extra={"COMFYUI_URL": "http://127.0.0.1:8188",
                                  "COMFYUI_PREFIX": "score_9, score_8_up,"},
                       home=tmp)
    doc = last_json(out)
    graph = doc["requests"][0]["body"]["prompt"] if doc and doc.get("requests") else None
    check("A15", "COMFYUI_PREFIX prepends to the positive prompt only",
          graph is not None
          and graph["6"]["inputs"]["text"] == "score_9, score_8_up, a knight"
          and graph["7"]["inputs"]["text"] == "")
    code, out, _ = run(["generate", "a knight", "--provider", "comfy", "--dry-run", "--json"],
                       env_extra={"COMFYUI_URL": "http://127.0.0.1:8188"}, home=tmp)
    doc = last_json(out)
    graph = doc["requests"][0]["body"]["prompt"] if doc and doc.get("requests") else None
    check("A15", "no prefix configured leaves the prompt untouched",
          graph is not None and graph["6"]["inputs"]["text"] == "a knight")

    # --- summary --------------------------------------------------------------
    print()
    print(f"{PASS} passed, {FAIL} failed "
          f"(offline set A1-A3, A6-A15; live smokes A4/A5 deferred-to-local)")
    if FAILURES:
        for f in FAILURES:
            print(f"  FAILED: {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
