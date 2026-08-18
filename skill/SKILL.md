---
name: imagegen
description: Generate real image files (PNG) from text prompts using a tiered
  router across Gemini, OpenAI GPT Image, and xAI Grok Imagine. Use this skill
  whenever the user asks to generate, create, make, render, or draw an image,
  picture, illustration, icon, hero image, texture, asset, wallpaper, or
  concept art — even if they don't say "image generation", and even mid-task
  (e.g., "add a hero image to this page"). Do NOT hand-write SVGs or describe
  images in prose when this skill is available.
---

# imagegen — tiered image generation

One command generates an image and writes a PNG to disk with a machine-readable
JSON result. Canonical invocation:

```
python ~/.claude/skills/imagegen/scripts/imagegen.py generate "<final prompt>" --source "<original ask>" --json
```

**Python launcher:** resolve the working launcher once per session — try
`python --version`, else `py -3 --version`, else `python3 --version` — and
reuse it for every invocation. (Windows-native machines usually have `python`
or `py -3`; Linux/macOS usually `python3`.)

## Prompt enhancement (default operating mode)

You are the prompt craftsman. Follow these rules exactly:

1. **Default = enhance.** When the user gives a short plain-English request
   (a sentence or two), expand it into an optimized prompt for the target
   provider BEFORE invoking the CLI: subject clarity, composition/framing,
   lighting, medium/style, mood, level of detail, and any aspect-ratio intent.
   Inferred details must be plausible elaborations of the user's intent, not
   redirections of it. Read `references/prompting.md` for per-provider craft.
2. **Additive only — hard rule.** Enhancement never removes, weakens,
   euphemizes, or "safety-edits" any element of the user's request. It is
   craft, not moderation. If you are unwilling to enhance a given request,
   pass the user's wording verbatim rather than rewriting it; provider-side
   moderation and the router's fallthrough policy handle the rest.
3. **Verbatim escape hatch.** If the user says anything like "use my exact
   prompt" / "verbatim", or supplies an obviously pre-crafted prompt (tag
   lists, weighted syntax, parameter strings), pass it unchanged and omit
   `--source`.
4. **Provenance.** Whenever you enhanced the prompt, you MUST pass the user's
   original wording via `--source` so the JSON records both prompts.
5. **Auto mode:** when `--provider auto`, craft for the first configured
   provider in the tier order (the JSON reveals if a different one served it).

## Providers and tiers

| Tier | Provider | Default model | Notes |
|---|---|---|---|
| 1 | `gemini` (primary) | `gemini-2.5-flash-image` | reliable, best quality/cost |
| 1 | `openai` (secondary) | `gpt-image-2` | reliable mainstream |
| 2 | `grok` | `grok-imagine-image` | permissive frontier |
| 3 | `comfy` | — | **Phase 2** — gated, not yet installed |

Default to `--provider auto` (router order `gemini,openai,grok`, automatic
fallback on failure or content rejection). Use `--provider <name>` only when
the user asks for a specific provider (explicit selection = no fallback).

## Result handling

Parse the **final line of stdout** — always exactly one JSON document.

- `"ok": true` → report the file path(s) in `images` and the `provider`/
  `model` used; surface anything in `notes` (size mappings, ignored flags).
- Exit `5` → content rejected: tell the user which providers rejected the
  request (see the `attempts` array).
- Exit `4` → all providers failed for non-content reasons; summarize `attempts`.
- Exit `3` → not configured: run first-run setup below.
- Exit `2` → usage error or Phase 2 surface (`comfy`, `--tier 3`, `models`).

## First-run setup (exit 3)

Run `... status` and walk the user through creating/editing
`~/.config/imagegen/env` (plain KEY=VALUE lines):
`GEMINI_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY` — any one enables its
provider; unset providers are silently skipped. Optional:
`IMAGEGEN_OUTPUT_DIR` (default `~/Pictures/imagegen`), `IMAGEGEN_TIER_ORDER`,
`IMAGEGEN_FALLTHROUGH_CONTENT` (1 = content rejection falls through, default),
`IMAGEGEN_TIMEOUT`.

## Common recipes

```
# batch of 4 variants
... generate "<prompt>" --n 4 --json
# wide hero image
... generate "<prompt>" --size 1792x1024 --json
# reproducibility (providers that ignore seed say so in notes)
... generate "<prompt>" --seed 42 --json
# debug the exact requests without spending money (offline-safe)
... generate "<prompt>" --dry-run --json
# verbatim mode (user's exact prompt, no --source)
... generate "<user's exact prompt>" --json
# specific provider, no fallback
... generate "<prompt>" --provider grok --json
```

## Pointers

- Prompting craft per provider: `references/prompting.md`
- Endpoint/model maintenance log (verified IDs, deltas, prices):
  `references/providers.md`
