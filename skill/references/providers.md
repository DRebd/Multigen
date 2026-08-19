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
- **Default model:** `gemini-3-pro-image` ("Nano Banana Pro"): ~$0.134/image
  at 1K/2K, ~$0.24 at 4K; best-in-class text rendering and highest quality.
  Owner decision 2026-08-18: default to Pro over the cheaper Flash line
  (quality over cost for the primary provider). The former `-preview` suffix
  is gone from the docs' model list — the plain ID is current.
- **Cheaper alternatives** (select with `--model`): `gemini-3.1-flash-image`
  and `gemini-3.1-flash-lite-image` (current Flash line, per-image pricing
  not yet verified locally), and legacy `gemini-2.5-flash-image`
  ("Nano Banana", ~$0.039/image).
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

## Moderation surfaces (design delta, 2026-08-19)

The PDR modeled moderation as a **provider** property (tier 1 strict → tier
3 none). Live use surfaced a second surface: the **operator**. When Claude
runs this skill it applies its own content policy above any SKILL.md
instruction — tier 3 removes provider moderation only, and no skill wording
binds the operator. Consequence, by design rather than workaround: content
the operator declines is served by **user-direct mode** — the standalone CLI
(`imagegen` PowerShell alias, `--pre` prompt blocks) or ComfyUI's own web UI
at `COMFYUI_URL` — with no Claude involvement. The router needs no changes;
the two modes share every other component.

## ComfyUI (tier 3) — local, implemented 2026-08-19

- **Enabled by:** `COMFYUI_URL` (e.g. `http://127.0.0.1:8188`). No auth —
  keep it loopback/LAN only (status warns on public plain-http URLs).
- **Endpoints used:** `POST /prompt` (submit workflow, returns `prompt_id`),
  `GET /history/{id}` (poll ~1s until `status.completed`),
  `GET /view?filename=&subfolder=&type=` (download PNGs),
  `GET /object_info/CheckpointLoaderSimple` (checkpoint list; also backs the
  `models` command). Verified live against ComfyUI master (2026-08-19).
- **Workflow:** standard txt2img graph — CheckpointLoaderSimple →
  CLIPTextEncode (pos from prompt, neg from `COMFYUI_NEGATIVE`) → KSampler →
  VAEDecode → SaveImage. `--n` maps to latent `batch_size`; `--seed` is
  honored (randomized when omitted, reported in `notes`); `--size` snaps to
  multiples of 8.
- **Checkpoint:** `--model <file.safetensors>` > `COMFYUI_CHECKPOINT` >
  first checkpoint on the server. The result `model` field is the
  checkpoint filename. Cost: always `0.0`.
- **Timeout:** `COMFYUI_TIMEOUT` (default 600s) — first generation after
  server start pays model-load time; explicit `--timeout` overrides.
- **Content:** local generation never classifies `CONTENT_REJECTED`.

### Per-machine tuning (same code + config keys, different values)

| Key | **PC** (RX 9070 XT 16GB, Win11, ROCm 7.2.1) | **personal server** (RX 5700 XT 8GB, Ubuntu 24.04) — planned |
|---|---|---|
| COMFYUI_URL | http://127.0.0.1:8188 | http://127.0.0.1:8188 |
| COMFYUI_CHECKPOINT | sd_xl_base_1.0.safetensors (fp16) | SD 1.5-class or quantized/GGUF checkpoint (8GB VRAM) |
| COMFYUI_STEPS | 25 | 20 |
| COMFYUI_CFG | 7.0 | 7.0 |
| COMFYUI_SAMPLER / SCHEDULER | euler / normal | euler / normal |
| default --size | 1024x1024 | 512x512 (SD1.5) |

Server caveat (unverified until that box is online): the RX 5700 XT is
RDNA1 (`gfx1010`), which current ROCm releases no longer support. Plan A is
ComfyUI on Vulkan-based PyTorch or `HSA_OVERRIDE_GFX_VERSION` on an older
ROCm; plan B is stable-diffusion.cpp (Vulkan) behind a ComfyUI-compatible
shim. Decide when the server is up — the imagegen adapter only needs the
ComfyUI HTTP API to exist at `COMFYUI_URL`.

### PC install record (2026-08-19)

ComfyUI at `C:\Users\DRR\ComfyUI` (master), venv Python 3.12.10, AMD
official wheels: rocm-sdk 7.2.1 + torch 2.9.1+rocm7.2.1 from
`repo.radeon.com/rocm/windows/rocm-rel-7.2.1` (requires Adrenalin >= 26.2.2).
Checkpoint: `models\checkpoints\sd_xl_base_1.0.safetensors` (official
stabilityai SDXL base 1.0, 6.9GB). Launch:
`venv\Scripts\python.exe main.py --listen 127.0.0.1 --port 8188`.

## Price table in the script

| Model | USD/image |
|---|---|
| gemini-3-pro-image | 0.134 (1K/2K) |
| gemini-2.5-flash-image | 0.039 |
| gpt-image-2 | 0.03 / 0.05 / 0.08 by resolution |
| gpt-image-1.5 | 0.009 (low, 1024²) |
| gpt-image-1 | 0.02 (low) — deprecates 2026-10-23 |
| grok-imagine-image | 0.02 |
| grok-imagine-image-quality | 0.05 |
| grok-imagine-image-2.0 | 0.04 |
| comfy (any local checkpoint) | 0.00 |
