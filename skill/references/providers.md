# Provider maintenance log

Verified facts, chosen model IDs, and doc-vs-PDR deltas. This is the
maintenance log for `scripts/imagegen.py`; update it whenever endpoints,
model IDs, or prices change. Prices baked into the script are stamped
`PRICES_AS_OF = 2026-08-18`.

**Verification note (2026-08-18, cloud build session):** the sandbox's
egress proxy blocked direct fetches of `ai.google.dev`,
`platform.openai.com`, and `docs.x.ai`; facts below were verified via web
search against official-doc mirrors and reputable secondary sources
(developers.openai.com and docs.x.ai excerpts surfaced in search results),
per PDR §16.1 the `hex/claude-image-generation` reference tables were
treated as secondary only. Re-verify against the primary docs at the first
local session (T11).

## Gemini (tier 1 primary)

- **Endpoint:** `POST https://generativelanguage.googleapis.com/v1beta/models/<MODEL>:generateContent`
- **Auth:** `x-goog-api-key: <key>` header. Verified current.
- **Default model:** `gemini-2.5-flash-image` (GA, "Nano Banana"). ~$0.039/image.
- **Newer model available:** `gemini-3-pro-image-preview` ("Nano Banana Pro",
  GA ~June 2026 but the ID kept its `-preview` suffix): ~$0.134/image at
  1K/2K, ~$0.24 at 4K; best-in-class text rendering. **Delta vs PDR §6.1:**
  the PDR says "prefer the newest GA image model"; we default to
  `gemini-2.5-flash-image` because tier 1's charter is best quality/cost and
  the newer ID's `-preview` suffix makes its GA status ambiguous in the docs.
  Select the Pro model with `--model gemini-3-pro-image-preview`.
  (A `gemini-3.1-flash-image` / "Nano Banana 2" sibling was reported but not
  verified to a concrete API ID; re-check locally.)
- **Request:** `contents[0].parts[]` (text + optional `inline_data` image refs);
  `generationConfig.responseModalities: ["IMAGE"]`;
  `generationConfig.imageConfig.aspectRatio: "16:9"` etc.
- **Aspect ratios (GA set):** 21:9, 16:9, 4:3, 3:2, 5:4, 1:1, 4:5, 3:4, 2:3,
  9:16. 16:9 → 1344×768 px. `--size` maps to the nearest ratio.
- **Response:** `candidates[0].content.parts[].inlineData.data` (base64).
  Safety blocks appear as `promptFeedback.blockReason` or candidate
  `finishReason` in {SAFETY, IMAGE_SAFETY, PROHIBITED_CONTENT} → classified
  `CONTENT_REJECTED`.
- **n:** one image per generateContent call; the adapter loops `--n` times.
- **seed:** not supported for image output → noted as ignored.
- **refs:** supported (`inline_data` parts) — the only v1 provider with
  `--ref` support.

## OpenAI GPT Image (tier 1 secondary)

- **Endpoint:** `POST https://api.openai.com/v1/images/generations`
- **Auth:** `Authorization: Bearer <key>`.
- **Default model:** `gpt-image-2`. **Delta vs PDR §6.2:** the PDR names
  `gpt-image-1`, but as of 2026-08 `gpt-image-2` is the newest GA image
  model and `gpt-image-1` deprecates 2026-10-23 (`gpt-image-1.5` removal
  2026-12-01), so per the PDR's own "use newest GA image model" rule the
  default is `gpt-image-2`.
- **Sizes:** `gpt-image-2` accepts arbitrary `WxH` (both divisible by 16,
  aspect between 1:3 and 3:1, max 3840×2160); `gpt-image-1`/`-1.5` accept
  only 1024×1024, 1536×1024, 1024×1536. The adapter maps `--size`
  accordingly per model.
- **Request:** `{model, prompt, n, size, output_format: "png"}`. GPT image
  models always return base64 (`data[].b64_json`); no `response_format`
  param needed.
- **Pricing (gpt-image-2):** $0.03 (≤1K), $0.05 (≤2K), $0.08 (4K) per image.
- **seed:** not supported → noted as ignored. **refs:** image input is the
  separate `/v1/images/edits` endpoint → `--ref` unsupported in v1.
- **Content rejections:** HTTP 400 with `moderation`/`content_policy`
  wording → `CONTENT_REJECTED`.

## xAI Grok Imagine (tier 2)

- **Endpoint:** `POST https://api.x.ai/v1/images/generations`
  (OpenAI-compatible). Verified current.
- **Auth:** `Authorization: Bearer <key>`.
- **Models:** `grok-imagine-image` (default, ~$0.02/img at 1K) and
  `grok-imagine-image-quality` (~$0.05/img), both confirmed live.
  `grok-imagine-image-2.0` also exists (~$0.04–0.08 by quality/resolution)
  and is selectable via `--model`. PDR note confirmed:
  `grok-imagine-image-pro` deprecated May 2026 — do not use.
- **Request:** `{model, prompt, n, response_format: "b64_json"}`. `n` up to
  the API's batch limit (we cap at 4 via `--n`).
- **Response:** `data[].b64_json` (may arrive with a `data:image/png;base64,`
  prefix — the adapter strips it) or `data[].url` (adapter downloads it).
- **size/aspect:** newer imagine models reportedly accept aspect/resolution
  controls but the official parameter name was not verifiable from the
  sandbox; v1 sends no size parameter and notes this in the JSON. Re-check
  at docs.x.ai locally and update the adapter if a param is confirmed.
- **seed:** not verifiable → not sent, noted as ignored.
- **Moderation:** xAI runs automated moderation on prompts and outputs;
  rejections classify as `CONTENT_REJECTED` and fall through in auto mode
  when `IMAGEGEN_FALLTHROUGH_CONTENT=1`.

## ComfyUI (tier 3) — Phase 2

Not implemented in v1. Build spec preserved in PDR §6.4 (endpoints:
`/system_stats`, `/object_info`, `/prompt`, `/history/{id}`, `/view`).
Doc verification (docs.comfy.org) deferred to the Phase 2 session.

## Price table in the script

| Model | USD/image |
|---|---|
| gemini-2.5-flash-image | 0.039 |
| gemini-3-pro-image-preview | 0.134 (1K/2K) |
| gpt-image-2 | 0.03 / 0.05 / 0.08 by resolution |
| gpt-image-1.5 | 0.009 (low, 1024²) |
| gpt-image-1 | 0.02 (low) — deprecates 2026-10-23 |
| grok-imagine-image | 0.02 |
| grok-imagine-image-quality | 0.05 |
| grok-imagine-image-2.0 | 0.04 |
