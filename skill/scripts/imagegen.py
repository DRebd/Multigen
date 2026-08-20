#!/usr/bin/env python3
"""imagegen — tiered image-generation CLI for the Claude Code `imagegen` skill.

Routes a text prompt across image providers in tier order with automatic
fallback, writes PNG file(s) to disk, and prints a single-line JSON result
document as the final line of stdout.

Tiers (v2):
  1  gemini (primary), openai (secondary)   — reliable mainstream
  2  grok (xAI Grok Imagine)                — permissive frontier
  3  comfy (local ComfyUI server)           — local, free, private

Design rules (per PDR):
  * stdlib only, single file, Python 3.10+
  * all network I/O via urllib.request with explicit timeouts
  * structured errors (never exceptions) cross the adapter boundary
  * the CLI never rewrites prompts — verbatim transport
  * all file writing goes through write_outputs() (Phase 2 upscale seam)
  * Windows-native safe: pathlib paths, best-effort chmod, ASCII output
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

VERSION = "2.2.0"

# Best-effort price table (USD per image). Verified against docs on the date
# below; see references/providers.md for sources and deltas.
PRICES_AS_OF = "2026-08-18"
PRICES = {
    "gemini-3-pro-image": 0.134,  # 1K/2K; 4K is 0.24
    "gemini-2.5-flash-image": 0.039,
    "gpt-image-2": 0.03,       # 1K; 2K 0.05, 4K 0.08 (see _openai_cost)
    "gpt-image-1.5": 0.009,    # low/1024x1024 baseline
    "gpt-image-1": 0.02,       # low-quality baseline (deprecates 2026-10-23)
    "grok-imagine-image": 0.02,
    "grok-imagine-image-quality": 0.05,
    "grok-imagine-image-2.0": 0.04,
}

TIER_OF = {"gemini": 1, "openai": 1, "grok": 2, "comfy": 3}
IMPLEMENTED = ("gemini", "openai", "grok", "comfy")
DEFAULT_TIER_ORDER = "gemini,openai,grok,comfy"

# comfy is enabled by COMFYUI_URL rather than an API key (local server, no auth)
KEY_VAR = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY",
           "grok": "XAI_API_KEY", "comfy": "COMFYUI_URL"}

DEFAULT_MODEL = {
    "gemini": "gemini-3-pro-image",
    "openai": "gpt-image-2",
    "grok": "grok-imagine-image",
    "comfy": "comfy-local",  # placeholder; real model = resolved checkpoint name
}

# Error taxonomy (the only values allowed in error_class)
NOT_CONFIGURED = "NOT_CONFIGURED"
AUTH = "AUTH"
RATE_LIMIT = "RATE_LIMIT"
CONTENT_REJECTED = "CONTENT_REJECTED"
PROVIDER_ERROR = "PROVIDER_ERROR"
USAGE = "USAGE"
ERROR_CLASSES = {NOT_CONFIGURED, AUTH, RATE_LIMIT, CONTENT_REJECTED, PROVIDER_ERROR, USAGE}

# Gemini supported aspect ratios (GA set) as (name, w/h ratio)
GEMINI_ASPECTS = [
    ("21:9", 21 / 9), ("16:9", 16 / 9), ("4:3", 4 / 3), ("3:2", 3 / 2),
    ("5:4", 5 / 4), ("1:1", 1.0), ("4:5", 4 / 5), ("3:4", 3 / 4),
    ("2:3", 2 / 3), ("9:16", 9 / 16),
]

# Discrete sizes for gpt-image-1 / gpt-image-1.5
OPENAI_DISCRETE_SIZES = [(1024, 1024), (1536, 1024), (1024, 1536)]

CONFIG_DIR = Path(os.path.expanduser("~")) / ".config" / "imagegen"
CONFIG_FILE = CONFIG_DIR / "env"
LOG_FILE = CONFIG_DIR / "imagegen.log"
DEFAULT_OUTPUT_DIR = str(Path(os.path.expanduser("~")) / "Pictures" / "imagegen")

# 1x1 transparent PNG used only by the IMAGEGEN_MOCK=<provider>:OK test hook.
MOCK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

def parse_env_file(path: Path) -> dict:
    """Parse a plain KEY=VALUE file (# comments allowed). No dotenv dep."""
    out = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            out[key] = val
    return out


def load_config() -> dict:
    """Merge config sources. Precedence: process env > config file > defaults.
    (CLI flags override at request-build time.)"""
    cfg = {
        "GEMINI_API_KEY": "",
        "OPENAI_API_KEY": "",
        "XAI_API_KEY": "",
        "IMAGEGEN_OUTPUT_DIR": DEFAULT_OUTPUT_DIR,
        "IMAGEGEN_TIER_ORDER": DEFAULT_TIER_ORDER,
        "IMAGEGEN_FALLTHROUGH_CONTENT": "1",
        "IMAGEGEN_TIMEOUT": "120",
        "COMFYUI_URL": "",          # presence enables the comfy provider
        "COMFYUI_CHECKPOINT": "",   # empty = first checkpoint on the server
        "COMFYUI_STEPS": "25",      # per-machine tuning (see providers.md)
        "COMFYUI_CFG": "7.0",
        "COMFYUI_SAMPLER": "euler",
        "COMFYUI_SCHEDULER": "normal",
        "COMFYUI_NEGATIVE": "",
        "COMFYUI_PREFIX": "",       # auto-prepended to every comfy prompt (e.g. Pony score tags)
        "COMFYUI_CLIP_SKIP": "1",   # 1 = last layer (SDXL/realistic); 2 = Pony/Illustrious/NoobAI
        "COMFYUI_VAE": "",          # empty = checkpoint's baked VAE; else a VAE filename
        "COMFYUI_TIMEOUT": "600",   # local generation incl. first model load
        "IMAGEGEN_UPSCALE": "0",    # Phase 2 — recognized, inert
    }
    cfg.update({k: v for k, v in parse_env_file(CONFIG_FILE).items() if k in cfg})
    cfg.update({k: os.environ[k] for k in cfg if k in os.environ})
    return cfg


def configured(cfg: dict, provider: str) -> bool:
    if provider == "comfy":
        return bool(cfg.get("COMFYUI_URL", ""))
    return bool(cfg.get(KEY_VAR.get(provider, ""), ""))


# ----------------------------------------------------------------------------
# Logging (append-only; never prompts, never keys)
# ----------------------------------------------------------------------------

def rotate_log() -> None:
    try:
        if not LOG_FILE.exists():
            return
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > 1000:
            LOG_FILE.write_text("\n".join(lines[-500:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def log_line(msg: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {msg}\n")
    except OSError:
        pass


def progress(msg: str) -> None:
    print(f"[imagegen] {msg}", file=sys.stderr)


# ----------------------------------------------------------------------------
# HTTP (urllib only, explicit timeouts)
# ----------------------------------------------------------------------------

def http_post_json(url: str, headers: dict, body: dict, timeout: int):
    """POST JSON. Returns (status:int|None, payload:dict|None, detail:str, retry_after:int|None).
    status None means transport failure (timeout / connection error)."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw.decode("utf-8")), "", None
            except (ValueError, UnicodeDecodeError):
                return resp.status, None, "malformed (non-JSON) response body", None
    except urllib.error.HTTPError as e:
        raw = b""
        try:
            raw = e.read()
        except OSError:
            pass
        detail = raw.decode("utf-8", errors="replace")[:2000]
        retry_after = None
        try:
            ra = e.headers.get("Retry-After") if e.headers else None
            if ra and ra.strip().isdigit():
                retry_after = int(ra.strip())
        except Exception:
            pass
        return e.code, _try_json(detail), detail, retry_after
    except urllib.error.URLError as e:
        return None, None, f"connection error: {getattr(e, 'reason', e)}", None
    except TimeoutError:
        return None, None, "request timed out", None
    except OSError as e:
        return None, None, f"transport error: {e}", None


def http_get_json(url: str, timeout: int):
    """GET JSON. Returns (status:int|None, payload:dict|None, detail:str)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw.decode("utf-8")), ""
            except (ValueError, UnicodeDecodeError):
                return resp.status, None, "malformed (non-JSON) response body"
    except urllib.error.HTTPError as e:
        raw = b""
        try:
            raw = e.read()
        except OSError:
            pass
        return e.code, None, raw.decode("utf-8", errors="replace")[:2000]
    except urllib.error.URLError as e:
        return None, None, f"connection error: {getattr(e, 'reason', e)}"
    except TimeoutError:
        return None, None, "request timed out"
    except OSError as e:
        return None, None, f"transport error: {e}"


def http_get_bytes(url: str, timeout: int):
    """GET raw bytes (used to download URL-form image results)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read(), ""
    except Exception as e:  # classified upstream as PROVIDER_ERROR
        return None, f"image download failed: {e}"


def _try_json(text: str):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def looks_content_rejected(status, text: str) -> bool:
    if status not in (400, 403, 422):
        return False
    t = (text or "").lower()
    keywords = ("safety", "content_policy", "content policy", "moderation",
                "moderated", "blocked", "prohibited", "image_safety",
                "policy_violation", "rejected by")
    return any(k in t for k in keywords)


def classify_http(status, detail: str):
    """Map an HTTP failure to an error class (content check done separately)."""
    if status is None:
        return PROVIDER_ERROR
    if status in (401, 403):
        # 403 with moderation wording is CONTENT_REJECTED; caller checks first
        return AUTH
    if status == 429:
        return RATE_LIMIT
    if status >= 500:
        return PROVIDER_ERROR
    if looks_content_rejected(status, detail):
        return CONTENT_REJECTED
    if status == 400 or status == 422:
        return PROVIDER_ERROR
    return PROVIDER_ERROR


# ----------------------------------------------------------------------------
# Size mapping helpers
# ----------------------------------------------------------------------------

def parse_size(size: str):
    m = re.fullmatch(r"(\d{2,5})x(\d{2,5})", size.strip().lower())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def gemini_aspect_for(w: int, h: int):
    ratio = w / h
    return min(GEMINI_ASPECTS, key=lambda a: abs(a[1] - ratio))[0]


def openai_size_for(model: str, w: int, h: int):
    """Nearest supported size string for the given OpenAI image model."""
    if model.startswith("gpt-image-2"):
        # arbitrary WxH: both divisible by 16, ratio within [1:3, 3:1], max 3840x2160
        ratio = max(1 / 3, min(3.0, w / h))
        if w / h != ratio:  # clamp the longer edge to restore a legal ratio
            if w > h:
                w = int(h * ratio)
            else:
                h = int(w / ratio)
        w = max(256, min(3840, round(w / 16) * 16))
        h = max(256, min(2160, round(h / 16) * 16))
        return f"{w}x{h}"
    best = min(OPENAI_DISCRETE_SIZES, key=lambda s: abs((s[0] / s[1]) - (w / h)))
    return f"{best[0]}x{best[1]}"


# ----------------------------------------------------------------------------
# Adapters — pure functions: (cfg, req) -> ProviderResult dict
# ProviderResult: {ok, model, images: [bytes], notes: [str]}
#             or  {ok: False, model, error_class, detail, retry_after?}
# Each adapter also has a build_<p>_requests(cfg, req) used by --dry-run.
# ----------------------------------------------------------------------------

def _guess_mime(path: str) -> str:
    ext = Path(path).suffix.lower()
    return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "image/png")


def build_gemini_requests(cfg, req):
    model = req["model"] or DEFAULT_MODEL["gemini"]
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent")
    parts = [{"text": req["prompt"]}]
    for ref in req["refs"]:
        try:
            data = base64.b64encode(Path(ref).read_bytes()).decode("ascii")
        except OSError:
            data = "<unreadable>"
        parts.append({"inline_data": {"mime_type": _guess_mime(ref), "data": data}})
    w, h = req["size"]
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": gemini_aspect_for(w, h)},
        },
    }
    headers = {"x-goog-api-key": cfg["GEMINI_API_KEY"] or "***"}
    # Gemini returns one image per generateContent call: n calls for --n
    return [{"url": url, "method": "POST", "headers": headers, "body": body}
            for _ in range(req["n"])]


def generate_gemini(cfg, req):
    model = req["model"] or DEFAULT_MODEL["gemini"]
    notes = []
    w, h = req["size"]
    aspect = gemini_aspect_for(w, h)
    if f"{w}:{h}" != aspect:
        notes.append(f"--size {w}x{h} mapped to gemini aspect ratio {aspect}")
    if req["seed"] is not None:
        notes.append("--seed ignored by gemini")
    images = []
    for call in build_gemini_requests(cfg, req):
        status, payload, detail, retry_after = http_post_json(
            call["url"], call["headers"], call["body"], req["timeout"])
        if status != 200:
            if looks_content_rejected(status, detail):
                return {"ok": False, "model": model, "error_class": CONTENT_REJECTED,
                        "detail": _short(detail) or f"HTTP {status}"}
            return {"ok": False, "model": model,
                    "error_class": classify_http(status, detail),
                    "detail": _short(detail) or f"HTTP {status} from generativelanguage.googleapis.com",
                    "retry_after": retry_after}
        img, why = _gemini_extract_image(payload)
        if img is None:
            cls = CONTENT_REJECTED if why == "blocked" else PROVIDER_ERROR
            return {"ok": False, "model": model, "error_class": cls,
                    "detail": why if why != "blocked" else "response blocked by gemini safety filters"}
        images.append(img)
    return {"ok": True, "model": model, "images": images, "notes": notes}


def _gemini_extract_image(payload):
    if not isinstance(payload, dict):
        return None, "malformed response"
    fb = payload.get("promptFeedback") or {}
    if fb.get("blockReason"):
        return None, "blocked"
    cands = payload.get("candidates") or []
    if not cands:
        return None, "blocked" if fb else "no candidates in response"
    cand = cands[0]
    if str(cand.get("finishReason", "")).upper() in (
            "SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
        return None, "blocked"
    for part in (cand.get("content") or {}).get("parts") or []:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            try:
                return base64.b64decode(inline["data"]), ""
            except (ValueError, TypeError):
                return None, "undecodable image data in response"
    return None, "no image data in response"


def build_openai_requests(cfg, req):
    model = req["model"] or DEFAULT_MODEL["openai"]
    w, h = req["size"]
    body = {"model": model, "prompt": req["prompt"], "n": req["n"],
            "size": openai_size_for(model, w, h), "output_format": "png"}
    headers = {"Authorization": f"Bearer {cfg['OPENAI_API_KEY'] or '***'}"}
    return [{"url": "https://api.openai.com/v1/images/generations",
             "method": "POST", "headers": headers, "body": body}]


def generate_openai(cfg, req):
    model = req["model"] or DEFAULT_MODEL["openai"]
    notes = []
    w, h = req["size"]
    mapped = openai_size_for(model, w, h)
    if mapped != f"{w}x{h}":
        notes.append(f"--size {w}x{h} mapped to {mapped} for {model}")
    if req["seed"] is not None:
        notes.append("--seed ignored by openai")
    call = build_openai_requests(cfg, req)[0]
    status, payload, detail, retry_after = http_post_json(
        call["url"], call["headers"], call["body"], req["timeout"])
    if status != 200:
        if looks_content_rejected(status, detail):
            return {"ok": False, "model": model, "error_class": CONTENT_REJECTED,
                    "detail": _short(detail) or f"HTTP {status}"}
        return {"ok": False, "model": model,
                "error_class": classify_http(status, detail),
                "detail": _short(detail) or f"HTTP {status} from api.openai.com",
                "retry_after": retry_after}
    images = []
    for item in (payload or {}).get("data") or []:
        b64 = item.get("b64_json")
        if b64:
            try:
                images.append(base64.b64decode(_strip_data_uri(b64)))
            except (ValueError, TypeError):
                return {"ok": False, "model": model, "error_class": PROVIDER_ERROR,
                        "detail": "undecodable b64_json in response"}
    if not images:
        return {"ok": False, "model": model, "error_class": PROVIDER_ERROR,
                "detail": "no image data in response"}
    return {"ok": True, "model": model, "images": images, "notes": notes}


def build_grok_requests(cfg, req):
    model = req["model"] or DEFAULT_MODEL["grok"]
    body = {"model": model, "prompt": req["prompt"], "n": req["n"],
            "response_format": "b64_json"}
    headers = {"Authorization": f"Bearer {cfg['XAI_API_KEY'] or '***'}"}
    return [{"url": "https://api.x.ai/v1/images/generations",
             "method": "POST", "headers": headers, "body": body}]


def generate_grok(cfg, req):
    model = req["model"] or DEFAULT_MODEL["grok"]
    notes = ["--size not supported by grok image API; provider default resolution used"]
    if req["seed"] is not None:
        notes.append("--seed ignored by grok")
    call = build_grok_requests(cfg, req)[0]
    status, payload, detail, retry_after = http_post_json(
        call["url"], call["headers"], call["body"], req["timeout"])
    if status != 200:
        if looks_content_rejected(status, detail):
            return {"ok": False, "model": model, "error_class": CONTENT_REJECTED,
                    "detail": _short(detail) or f"HTTP {status}"}
        return {"ok": False, "model": model,
                "error_class": classify_http(status, detail),
                "detail": _short(detail) or f"HTTP {status} from api.x.ai",
                "retry_after": retry_after}
    images = []
    for item in (payload or {}).get("data") or []:
        b64 = item.get("b64_json")
        if b64:
            try:
                images.append(base64.b64decode(_strip_data_uri(b64)))
                continue
            except (ValueError, TypeError):
                return {"ok": False, "model": model, "error_class": PROVIDER_ERROR,
                        "detail": "undecodable b64_json in response"}
        if item.get("url"):
            data, why = http_get_bytes(item["url"], req["timeout"])
            if data is None:
                return {"ok": False, "model": model, "error_class": PROVIDER_ERROR,
                        "detail": why}
            images.append(data)
    if not images:
        return {"ok": False, "model": model, "error_class": PROVIDER_ERROR,
                "detail": "no image data in response"}
    return {"ok": True, "model": model, "images": images, "notes": notes}


def _comfy_base(cfg) -> str:
    return (cfg["COMFYUI_URL"] or "http://<COMFYUI_URL-unset>").rstrip("/")


def _comfy_positive(cfg, prompt: str) -> str:
    """COMFYUI_PREFIX + prompt. Checkpoint families that need a fixed lead-in
    (Pony's score_* tags, booru quality boosters) set it once in config so it
    travels with the checkpoint rather than being retyped per prompt."""
    prefix = (cfg["COMFYUI_PREFIX"] or "").strip()
    if not prefix:
        return prompt
    return f"{prefix.rstrip(', ')}, {prompt}"


def _comfy_size(w: int, h: int):
    """Latent dims must be multiples of 8; clamp to a sane local range."""
    snap = lambda v: max(64, min(4096, round(v / 8) * 8))
    return snap(w), snap(h)


def _comfy_workflow(cfg, req, ckpt: str, seed: int) -> dict:
    """Standard checkpoint txt2img graph in the ComfyUI /prompt API format.

    CLIP source is routed through a CLIPSetLastLayer node so COMFYUI_CLIP_SKIP
    works for Pony/Illustrious/NoobAI (which expect skip 2); the default of 1
    leaves the last layer in place (no-op for SDXL/realistic checkpoints).
    VAE decode uses an external VAE when COMFYUI_VAE names one, else the
    checkpoint's baked VAE."""
    w, h = _comfy_size(*req["size"])
    clip_skip = _int_or(cfg["COMFYUI_CLIP_SKIP"], 1)
    stop_at = -abs(clip_skip) if clip_skip else -1   # ComfyUI: -1 = last layer
    vae_name = (cfg["COMFYUI_VAE"] or "").strip()
    vae_src = ["11", 0] if vae_name else ["4", 2]
    positive = _comfy_positive(cfg, req["prompt"])
    graph = {
        "4": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": ckpt}},
        "10": {"class_type": "CLIPSetLastLayer",
               "inputs": {"stop_at_clip_layer": stop_at, "clip": ["4", 1]}},
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": w, "height": h, "batch_size": req["n"]}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"text": positive, "clip": ["10", 0]}},
        "7": {"class_type": "CLIPTextEncode",
              "inputs": {"text": cfg["COMFYUI_NEGATIVE"], "clip": ["10", 0]}},
        "3": {"class_type": "KSampler",
              "inputs": {"seed": seed,
                         "steps": _int_or(cfg["COMFYUI_STEPS"], 25),
                         "cfg": _float_or(cfg["COMFYUI_CFG"], 7.0),
                         "sampler_name": cfg["COMFYUI_SAMPLER"] or "euler",
                         "scheduler": cfg["COMFYUI_SCHEDULER"] or "normal",
                         "denoise": 1.0, "model": ["4", 0],
                         "positive": ["6", 0], "negative": ["7", 0],
                         "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode",
              "inputs": {"samples": ["3", 0], "vae": vae_src}},
        "9": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": "imagegen", "images": ["8", 0]}},
    }
    if vae_name:
        graph["11"] = {"class_type": "VAELoader",
                       "inputs": {"vae_name": vae_name}}
    return graph


def build_comfy_requests(cfg, req):
    ckpt = req["model"] or cfg["COMFYUI_CHECKPOINT"] or "<first server checkpoint>"
    seed = req["seed"] if req["seed"] is not None else 0  # randomized at run time
    body = {"prompt": _comfy_workflow(cfg, req, ckpt, seed), "client_id": "imagegen"}
    return [{"url": f"{_comfy_base(cfg)}/prompt", "method": "POST",
             "headers": {}, "body": body}]


def _comfy_checkpoints(cfg, timeout: int):
    """List ckpt_name choices from the server. Returns (names|None, detail)."""
    url = f"{_comfy_base(cfg)}/object_info/CheckpointLoaderSimple"
    status, payload, detail = http_get_json(url, timeout)
    if status != 200 or not isinstance(payload, dict):
        return None, _short(detail) or f"HTTP {status} from ComfyUI /object_info"
    try:
        names = payload["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
    except (KeyError, IndexError, TypeError):
        return None, "unexpected /object_info shape from ComfyUI"
    if not isinstance(names, list):
        return None, "unexpected /object_info shape from ComfyUI"
    return [n for n in names if isinstance(n, str)], ""


def generate_comfy(cfg, req):
    """ComfyUI: submit a txt2img graph, poll history, download outputs.
    Local generation never classifies as CONTENT_REJECTED."""
    base = _comfy_base(cfg)
    timeout = req["timeout"] if req.get("timeout_explicit") else _int_or(
        cfg["COMFYUI_TIMEOUT"], 600)
    poll_timeout = min(timeout, 15)

    ckpt = req["model"] or cfg["COMFYUI_CHECKPOINT"]
    if not ckpt:
        names, why = _comfy_checkpoints(cfg, poll_timeout)
        if names is None:
            return {"ok": False, "model": DEFAULT_MODEL["comfy"],
                    "error_class": PROVIDER_ERROR, "detail": why}
        if not names:
            return {"ok": False, "model": DEFAULT_MODEL["comfy"],
                    "error_class": PROVIDER_ERROR,
                    "detail": "no checkpoints installed on the ComfyUI server "
                              "(put a .safetensors in models/checkpoints)"}
        ckpt = names[0]

    seed = req["seed"] if req["seed"] is not None else random.randrange(2 ** 31)
    clip_skip = _int_or(cfg["COMFYUI_CLIP_SKIP"], 1)
    vae_name = (cfg["COMFYUI_VAE"] or "").strip()
    notes = [f"comfy: {ckpt} steps={_int_or(cfg['COMFYUI_STEPS'], 25)} "
             f"cfg={_float_or(cfg['COMFYUI_CFG'], 7.0)} "
             f"sampler={cfg['COMFYUI_SAMPLER'] or 'euler'}/"
             f"{cfg['COMFYUI_SCHEDULER'] or 'normal'} "
             f"clip_skip={clip_skip} vae={vae_name or 'baked'} seed={seed}"]
    if (cfg["COMFYUI_PREFIX"] or "").strip():
        notes.append("comfy: COMFYUI_PREFIX prepended to the positive prompt")
    w, h = _comfy_size(*req["size"])
    if (w, h) != tuple(req["size"]):
        notes.append(f"--size {req['size'][0]}x{req['size'][1]} snapped to {w}x{h} "
                     "(multiples of 8)")

    body = {"prompt": _comfy_workflow(cfg, req, ckpt, seed), "client_id": "imagegen"}
    status, payload, detail, _ra = http_post_json(f"{base}/prompt", {}, body,
                                                  poll_timeout)
    if status != 200 or not isinstance(payload, dict) or not payload.get("prompt_id"):
        return {"ok": False, "model": ckpt, "error_class": PROVIDER_ERROR,
                "detail": _comfy_error_detail(payload, detail, status)}
    prompt_id = payload["prompt_id"]

    deadline = time.monotonic() + timeout
    entry = None
    while time.monotonic() < deadline:
        status, hist, detail = http_get_json(f"{base}/history/{prompt_id}",
                                             poll_timeout)
        if status == 200 and isinstance(hist, dict) and prompt_id in hist:
            candidate = hist[prompt_id]
            st = (candidate.get("status") or {})
            if st.get("completed") or st.get("status_str") in ("success", "error"):
                entry = candidate
                break
        elif status is None:
            return {"ok": False, "model": ckpt, "error_class": PROVIDER_ERROR,
                    "detail": _short(detail)}
        time.sleep(1.0)
    if entry is None:
        return {"ok": False, "model": ckpt, "error_class": PROVIDER_ERROR,
                "detail": f"generation did not finish within {timeout}s "
                          "(raise COMFYUI_TIMEOUT for first-load model warmup)"}
    st = entry.get("status") or {}
    if st.get("status_str") == "error":
        return {"ok": False, "model": ckpt, "error_class": PROVIDER_ERROR,
                "detail": _comfy_history_error(st)}

    images = []
    for node_out in (entry.get("outputs") or {}).values():
        for img in node_out.get("images") or []:
            if img.get("type") not in (None, "output"):
                continue  # skip previews/temp
            q = urllib.parse.urlencode({"filename": img.get("filename", ""),
                                        "subfolder": img.get("subfolder", ""),
                                        "type": img.get("type", "output")})
            data, why = http_get_bytes(f"{base}/view?{q}", poll_timeout)
            if data is None:
                return {"ok": False, "model": ckpt,
                        "error_class": PROVIDER_ERROR, "detail": why}
            images.append(data)
    if not images:
        return {"ok": False, "model": ckpt, "error_class": PROVIDER_ERROR,
                "detail": "no output images in ComfyUI history"}
    return {"ok": True, "model": ckpt, "images": images, "notes": notes}


def _comfy_error_detail(payload, detail, status):
    if isinstance(payload, dict):
        err = payload.get("error") or {}
        msg = err.get("message") or ""
        nodes = payload.get("node_errors") or {}
        node_msgs = "; ".join(
            e.get("message", "") for v in nodes.values()
            for e in (v.get("errors") or []) if isinstance(e, dict))
        combined = "; ".join(x for x in (msg, node_msgs) if x)
        if combined:
            return _short(combined)
    return _short(detail) or f"HTTP {status} from ComfyUI /prompt"


def _comfy_history_error(st):
    for m in reversed(st.get("messages") or []):
        if isinstance(m, list) and len(m) == 2 and m[0] == "execution_error":
            info = m[1] or {}
            return _short(f"{info.get('exception_type', 'error')}: "
                          f"{info.get('exception_message', '')}")
    return "ComfyUI reported an execution error"


ADAPTERS = {"gemini": generate_gemini, "openai": generate_openai,
            "grok": generate_grok, "comfy": generate_comfy}
BUILDERS = {"gemini": build_gemini_requests, "openai": build_openai_requests,
            "grok": build_grok_requests, "comfy": build_comfy_requests}


def _strip_data_uri(b64: str) -> str:
    if b64.startswith("data:"):
        _, _, rest = b64.partition(",")
        return rest
    return b64


def _short(detail: str) -> str:
    detail = (detail or "").strip().replace("\n", " ")
    return detail[:300]


def _int_or(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_or(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ----------------------------------------------------------------------------
# Cost estimation
# ----------------------------------------------------------------------------

def estimate_cost(provider: str, model: str, size, n: int):
    if provider == "comfy":
        return 0.0  # local generation
    if model.startswith("gpt-image-2"):
        longest = max(size)
        per = 0.03 if longest <= 1024 else (0.05 if longest <= 2048 else 0.08)
        return round(per * n, 4)
    per = PRICES.get(model)
    if per is None:
        per = PRICES.get(DEFAULT_MODEL.get(provider, ""), None)
    return round(per * n, 4) if per is not None else None


# ----------------------------------------------------------------------------
# Output seam — ALL file writing goes through here (Phase 2 upscale hook, F1)
# ----------------------------------------------------------------------------

def write_outputs(images: list, req: dict, cfg: dict, provider: str) -> list:
    """Write image bytes to disk; returns absolute paths as strings."""
    out = req["out"]
    paths = []
    if out:
        out_path = Path(os.path.expanduser(out))
        is_dir = out_path.is_dir() or str(out).endswith(("/", "\\"))
        if is_dir:
            out_path.mkdir(parents=True, exist_ok=True)
            paths = _auto_names(out_path, provider, len(images))
        else:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if len(images) == 1:
                paths = [out_path]
            else:
                stem, suffix = out_path.stem, out_path.suffix or ".png"
                paths = [out_path.with_name(f"{stem}_{i + 1}{suffix}")
                         for i in range(len(images))]
    else:
        out_dir = Path(os.path.expanduser(cfg["IMAGEGEN_OUTPUT_DIR"]))
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = _auto_names(out_dir, provider, len(images))
    written = []
    for path, data in zip(paths, images):
        path.write_bytes(data)
        written.append(str(path.resolve()))
    return written


def _auto_names(directory: Path, provider: str, count: int) -> list:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return [directory / f"imagegen_{stamp}_{provider}_{i + 1}.png"
            for i in range(count)]


# ----------------------------------------------------------------------------
# IMAGEGEN_MOCK test hook (inert unless the env var is set)
# ----------------------------------------------------------------------------

def mock_directive(provider: str):
    """Parse IMAGEGEN_MOCK=<provider>:<ERROR_CLASS|OK>[,...]; None if unset."""
    raw = os.environ.get("IMAGEGEN_MOCK", "")
    if not raw:
        return None
    for entry in raw.split(","):
        name, _, directive = entry.strip().partition(":")
        if name.strip() == provider and directive.strip():
            return directive.strip().upper()
    return None


# ----------------------------------------------------------------------------
# Router
# ----------------------------------------------------------------------------

def call_provider(provider: str, cfg: dict, req: dict) -> dict:
    """Invoke one adapter with the per-class retry rules of PDR section 7.1."""
    mock = mock_directive(provider)
    if mock is not None:
        if mock == "OK":
            return {"ok": True, "model": req["model"] or DEFAULT_MODEL[provider],
                    "images": [MOCK_PNG] * req["n"],
                    "notes": [f"mocked success for {provider} (IMAGEGEN_MOCK)"]}
        if mock in ERROR_CLASSES:
            return {"ok": False, "model": req["model"] or DEFAULT_MODEL[provider],
                    "error_class": mock,
                    "detail": f"mocked {mock} for {provider} (IMAGEGEN_MOCK)"}
        return {"ok": False, "model": req["model"] or DEFAULT_MODEL[provider],
                "error_class": PROVIDER_ERROR,
                "detail": f"unknown IMAGEGEN_MOCK directive: {mock}"}

    result = ADAPTERS[provider](cfg, req)
    if result["ok"]:
        return result
    if result["error_class"] == RATE_LIMIT:
        wait = min(result.get("retry_after") or 15, 15)
        progress(f"{provider} -> RATE_LIMIT, retrying once in {wait}s")
        time.sleep(wait)
        result = ADAPTERS[provider](cfg, req)
    elif result["error_class"] == PROVIDER_ERROR:
        progress(f"{provider} -> PROVIDER_ERROR, retrying once in 2s")
        time.sleep(2)
        result = ADAPTERS[provider](cfg, req)
    return result


def resolve_provider_list(cfg: dict, args) -> tuple:
    """Returns (providers, notes, error). error = (exit_code, error_class, detail)."""
    notes = []
    order = [p.strip().lower() for p in cfg["IMAGEGEN_TIER_ORDER"].split(",") if p.strip()]
    for p in order:
        if p not in TIER_OF:
            return [], notes, (3, USAGE,
                               f"unknown provider '{p}' in IMAGEGEN_TIER_ORDER "
                               f"(valid: gemini, openai, grok, comfy)")
    if args.provider != "auto":
        providers = [args.provider]
        if args.tier is not None:
            notes.append("--tier ignored because --provider was given explicitly")
    else:
        providers = order
        if args.tier is not None:
            providers = [p for p in providers if TIER_OF[p] == args.tier]
            if not providers:
                return [], notes, (3, USAGE,
                                   f"no tier-{args.tier} providers in IMAGEGEN_TIER_ORDER")
    if args.ref:
        capable = [p for p in providers if p == "gemini"]
        if args.provider != "auto" and args.provider != "gemini":
            return [], notes, (2, USAGE,
                               f"--ref is not supported by {args.provider} in v1 (gemini only)")
        if args.provider == "auto":
            if not capable:
                return [], notes, (2, USAGE,
                                   "--ref requires a provider with image input support "
                                   "(gemini only in v1)")
            notes.append("--ref restricts auto routing to providers with image input (gemini)")
            providers = capable
    return providers, notes, None


def run_generate(args) -> int:
    rotate_log()
    cfg = load_config()

    size = parse_size(args.size)
    if size is None:
        return finish(args, error_doc(USAGE, f"invalid --size '{args.size}' (expected WxH, e.g. 1024x1024)"), 2)
    for ref in args.ref or []:
        if not Path(os.path.expanduser(ref)).is_file():
            return finish(args, error_doc(USAGE, f"--ref file not found: {ref}"), 2)

    prompt = args.prompt
    pre_notes = []
    if args.pre:
        parts = []
        for pf in args.pre:
            path = Path(os.path.expanduser(pf))
            if not path.is_file():
                return finish(args, error_doc(USAGE, f"--pre file not found: {pf}"), 2)
            try:
                # utf-8-sig: Windows editors (Notepad, Set-Content -Encoding utf8)
                # write a BOM that would otherwise ride along into the prompt.
                parts.append(path.read_text(encoding="utf-8-sig", errors="replace").strip())
            except OSError as e:
                return finish(args, error_doc(USAGE, f"--pre file unreadable: {pf} ({e})"), 2)
        prompt = "\n".join(parts + [args.prompt])
        pre_notes.append("--pre prepended " + ", ".join(
            Path(os.path.expanduser(pf)).name for pf in args.pre))

    req = {
        "prompt": prompt,
        "source_prompt": args.source,
        "n": args.n,
        "size": size,
        "seed": args.seed,
        "model": args.model,
        "refs": [os.path.expanduser(r) for r in (args.ref or [])],
        "timeout": args.timeout if args.timeout is not None else _int_or(cfg["IMAGEGEN_TIMEOUT"], 120),
        "timeout_explicit": args.timeout is not None,
        "out": args.out,
    }

    providers, notes, err = resolve_provider_list(cfg, args)
    notes = pre_notes + notes
    if err:
        code, cls, detail = err
        return finish(args, error_doc(cls, detail, notes=notes), code)

    if args.dry_run:
        return dry_run(args, cfg, req, providers, notes)

    if not any(configured(cfg, p) for p in providers):
        vars_needed = ", ".join(KEY_VAR[p] for p in providers)
        return finish(args, {
            "ok": False, "images": [], "error_class": NOT_CONFIGURED,
            "detail": (f"no configured providers among [{', '.join(providers)}]; "
                       f"set {vars_needed} in the environment or {CONFIG_FILE}"),
            "notes": notes, "attempts": []}, 3)

    fallthrough = cfg["IMAGEGEN_FALLTHROUGH_CONTENT"] == "1"
    attempts = []
    start = time.monotonic()
    for p in providers:
        if not configured(cfg, p):
            if args.json:
                attempts.append({"provider": p, "error_class": NOT_CONFIGURED,
                                 "detail": f"{KEY_VAR[p]} not set"})
            continue
        progress(f"trying {p}...")
        result = call_provider(p, cfg, req)
        if result["ok"]:
            paths = write_outputs(result["images"], req, cfg, p)
            duration_ms = int((time.monotonic() - start) * 1000)
            model = result["model"]
            doc = {
                "ok": True, "provider": p, "tier": TIER_OF[p], "model": model,
                "images": paths, "prompt": req["prompt"],
                "source_prompt": req["source_prompt"],
                "size": f"{size[0]}x{size[1]}", "seed": req["seed"],
                "duration_ms": duration_ms,
                "est_cost_usd": estimate_cost(p, model, size, req["n"]),
                "notes": notes + result.get("notes", []),
                "attempts": attempts,
            }
            log_line(f"generate ok provider={p} model={model} n={req['n']} "
                     f"duration_ms={duration_ms} outputs={';'.join(paths)}")
            human = (f"[ok] {p} (tier {TIER_OF[p]}) wrote {len(paths)} image(s): "
                     f"{', '.join(paths)}")
            return finish(args, doc, 0, human)
        cls = result["error_class"]
        attempts.append({"provider": p, "error_class": cls,
                         "detail": result.get("detail", "")})
        log_line(f"generate attempt provider={p} error_class={cls}")
        nxt = _next_configured(providers, p, cfg)
        if cls == CONTENT_REJECTED and not fallthrough:
            progress(f"{p} -> CONTENT_REJECTED, fallthrough disabled, stopping")
            return finish(args, fail_doc(req, size, cls, result.get("detail", ""),
                                         notes, attempts, start), 5)
        if cls == USAGE:
            return finish(args, fail_doc(req, size, cls, result.get("detail", ""),
                                         notes, attempts, start), 2)
        progress(f"{p} -> {cls}" + (f", falling through to {nxt}" if nxt else ""))

    real = [a for a in attempts if a["error_class"] != NOT_CONFIGURED]
    all_content = bool(real) and all(a["error_class"] == CONTENT_REJECTED for a in real)
    code = 5 if all_content else 4
    detail = ("content rejected by every attempted provider" if all_content
              else "all attempted providers failed")
    log_line(f"generate failed error_class_summary={[a['error_class'] for a in real]}")
    top_cls = CONTENT_REJECTED if all_content else (real[-1]["error_class"] if real else PROVIDER_ERROR)
    return finish(args, fail_doc(req, size, top_cls, detail, notes, attempts, start), code)


def _next_configured(providers, current, cfg):
    idx = providers.index(current)
    for p in providers[idx + 1:]:
        if configured(cfg, p):
            return p
    return None


def fail_doc(req, size, error_class, detail, notes, attempts, start):
    return {
        "ok": False, "provider": None, "tier": None, "model": None, "images": [],
        "prompt": req["prompt"], "source_prompt": req["source_prompt"],
        "size": f"{size[0]}x{size[1]}", "seed": req["seed"],
        "duration_ms": int((time.monotonic() - start) * 1000),
        "est_cost_usd": None, "error_class": error_class, "detail": _short(detail),
        "notes": notes, "attempts": attempts,
    }


def error_doc(error_class, detail, notes=None):
    return {"ok": False, "images": [], "error_class": error_class,
            "detail": detail, "notes": notes or [], "attempts": []}


def dry_run(args, cfg, req, providers, notes) -> int:
    plans = []
    for p in providers:
        for call in BUILDERS[p](cfg, req):
            headers = dict(call["headers"])
            for k in list(headers):
                if k.lower() in ("authorization",):
                    headers[k] = "Bearer ***"
                elif k.lower() in ("x-goog-api-key",):
                    headers[k] = "***"
            body = call["body"]
            body = json.loads(json.dumps(body))  # deep copy
            _redact_inline_images(body)
            plans.append({"provider": p, "configured": configured(cfg, p),
                          "method": call["method"], "url": call["url"],
                          "headers": headers, "body": body})
    doc = {"ok": True, "dry_run": True, "images": [],
           "prompt": req["prompt"], "source_prompt": req["source_prompt"],
           "notes": notes, "requests": plans, "attempts": []}
    human = f"[dry-run] {len(plans)} request(s) across {len(providers)} provider(s); no network calls made"
    return finish(args, doc, 0, human)


def _redact_inline_images(body):
    if isinstance(body, dict):
        for k, v in body.items():
            if k in ("data",) and isinstance(v, str) and len(v) > 64:
                body[k] = f"<{len(v)} base64 chars>"
            else:
                _redact_inline_images(v)
    elif isinstance(body, list):
        for item in body:
            _redact_inline_images(item)


def finish(args, doc, code, human=None) -> int:
    """Emit exactly one JSON document as the final stdout line (plus, without
    --json, one friendly summary line before it)."""
    if not getattr(args, "json", False):
        if human is None:
            if doc.get("ok"):
                human = "[ok] done"
            else:
                human = f"[fail] {doc.get('error_class', 'ERROR')}: {doc.get('detail', '')}"
        print(human)
    print(json.dumps(doc, separators=(",", ":")))
    return code


# ----------------------------------------------------------------------------
# status command
# ----------------------------------------------------------------------------

def _rfc1918_or_loopback(host: str) -> bool:
    if host in ("localhost", "127.0.0.1", "::1", "[::1]"):
        return True
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)\.(\d+)", host)
    if not m:
        return False
    a, b = int(m.group(1)), int(m.group(2))
    return (a == 10 or a == 127 or (a == 172 and 16 <= b <= 31)
            or (a == 192 and b == 168))


def run_status(args) -> int:
    rotate_log()
    cfg = load_config()
    providers = {}
    for p in ("gemini", "openai", "grok"):
        var = KEY_VAR[p]
        if configured(cfg, p):
            providers[p] = {"tier": TIER_OF[p], "state": "configured",
                            "probe": "unprobed", "key": f"{var}=***set***"}
        else:
            providers[p] = {"tier": TIER_OF[p], "state": "not_configured",
                            "set_env_var": var}
    warnings = []
    if configured(cfg, "comfy"):
        providers["comfy"] = {
            "tier": 3, "state": "configured", "probe": "unprobed",
            "url": cfg["COMFYUI_URL"],
            "checkpoint": cfg["COMFYUI_CHECKPOINT"] or "auto (first server checkpoint)"}
        m = re.match(r"^(https?)://([^/:]+)", cfg["COMFYUI_URL"])
        if m and m.group(1) == "http" and not _rfc1918_or_loopback(m.group(2)):
            warnings.append(
                "COMFYUI_URL is a plain-http, non-loopback, non-private address; "
                "never expose ComfyUI unauthenticated to the public internet "
                "(use loopback, Tailscale, or an authenticated reverse proxy)")
    else:
        providers["comfy"] = {"tier": 3, "state": "not_configured",
                              "set_env_var": "COMFYUI_URL"}
    if os.name == "nt":
        warnings.append("config file permissions not enforced on this OS")
    elif CONFIG_FILE.exists():
        try:
            if CONFIG_FILE.stat().st_mode & 0o077:
                warnings.append(f"{CONFIG_FILE} is not mode 600; run: chmod 600 {CONFIG_FILE}")
        except OSError:
            pass

    any_configured = any(configured(cfg, p) for p in IMPLEMENTED)
    doc = {
        "ok": any_configured,
        "version": VERSION,
        "providers": providers,
        "tier_order": cfg["IMAGEGEN_TIER_ORDER"],
        "output_dir": cfg["IMAGEGEN_OUTPUT_DIR"],
        "config_file": str(CONFIG_FILE),
        "fallthrough_content": cfg["IMAGEGEN_FALLTHROUGH_CONTENT"] == "1",
        "prices_as_of": PRICES_AS_OF,
        "warnings": warnings,
    }
    if not any_configured:
        doc["error_class"] = NOT_CONFIGURED
        doc["detail"] = ("no providers configured; set GEMINI_API_KEY, "
                         "OPENAI_API_KEY, XAI_API_KEY, or COMFYUI_URL in the "
                         f"environment or in {CONFIG_FILE}")
    if not args.json:
        for p, info in providers.items():
            state = info["state"]
            extra = info.get("key") or info.get("set_env_var") or info.get("url") or ""
            print(f"  {p:<7} tier {info['tier']}  {state}  {extra}")
        for w in warnings:
            print(f"  warning: {w}")
    print(json.dumps(doc, separators=(",", ":")))
    return 0 if any_configured else 3


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

class JsonArgumentParser(argparse.ArgumentParser):
    """argparse that keeps the one-JSON-line-on-stdout contract on usage errors."""

    def error(self, message):
        print(json.dumps({"ok": False, "images": [], "error_class": USAGE,
                          "detail": message, "notes": [], "attempts": []},
                         separators=(",", ":")))
        print(f"error: {message}", file=sys.stderr)
        raise SystemExit(2)


def build_parser():
    parser = JsonArgumentParser(
        prog="imagegen.py",
        description="Tiered image generation: gemini -> openai -> grok, with fallback.")
    sub = parser.add_subparsers(dest="command")

    gen = sub.add_parser("generate", help="generate image(s) from a prompt")
    gen.add_argument("prompt")
    gen.add_argument("--provider", default="auto",
                     choices=["auto", "gemini", "openai", "grok", "comfy"])
    gen.add_argument("--tier", type=int, choices=[1, 2, 3])
    gen.add_argument("--out")
    gen.add_argument("--n", type=int, default=1, choices=[1, 2, 3, 4])
    gen.add_argument("--size", default="1024x1024")
    gen.add_argument("--seed", type=int)
    gen.add_argument("--model")
    gen.add_argument("--ref", action="append")
    gen.add_argument("--pre", action="append",
                     help="text file(s) prepended to the prompt, in order "
                          "(reusable style/character blocks you maintain yourself)")
    gen.add_argument("--source", help="original plain-English request (pre-enhancement)")
    gen.add_argument("--timeout", type=int)
    gen.add_argument("--json", action="store_true")
    gen.add_argument("--dry-run", action="store_true", dest="dry_run")
    # Phase 2 flag names deliberately NOT defined: --negative, --steps, --cfg,
    # --upscale, --fanout, --provider all

    st = sub.add_parser("status", help="show provider configuration")
    st.add_argument("--json", action="store_true")

    mo = sub.add_parser("models", help="list checkpoints on the ComfyUI server")
    mo.add_argument("--json", action="store_true")
    return parser


def run_models(args) -> int:
    cfg = load_config()
    if not configured(cfg, "comfy"):
        print(json.dumps({"ok": False, "images": [], "error_class": NOT_CONFIGURED,
                          "detail": "COMFYUI_URL is not set; the models command "
                                    "lists checkpoints on a ComfyUI server",
                          "notes": [], "attempts": []}, separators=(",", ":")))
        return 3
    names, why = _comfy_checkpoints(cfg, _int_or(cfg["IMAGEGEN_TIMEOUT"], 120))
    if names is None:
        print(json.dumps({"ok": False, "images": [], "error_class": PROVIDER_ERROR,
                          "detail": why, "notes": [], "attempts": []},
                         separators=(",", ":")))
        return 4
    if not getattr(args, "json", False):
        for n in names:
            print(f"  {n}")
    print(json.dumps({"ok": True, "url": cfg["COMFYUI_URL"],
                      "checkpoints": names, "count": len(names)},
                     separators=(",", ":")))
    return 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "generate":
        return run_generate(args)
    if args.command == "status":
        return run_status(args)
    if args.command == "models":
        return run_models(args)
    parser.error("a command is required: generate | status | models")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
