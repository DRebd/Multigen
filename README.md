# imagegen — tiered image-generation skill for Claude Code

A Claude Code **skill** backed by a single **stdlib-only Python CLI** that
generates images by routing prompts across tiers of providers, in order of
preference, with automatic fallback:

| Tier | Provider | Default model | Enabled by |
|---|---|---|---|
| 1 | Google Gemini (primary) | `gemini-2.5-flash-image` | `GEMINI_API_KEY` |
| 1 | OpenAI GPT Image (secondary) | `gpt-image-2` | `OPENAI_API_KEY` |
| 2 | xAI Grok Imagine | `grok-imagine-image` | `XAI_API_KEY` |
| 3 | ComfyUI | — | **Phase 2** (architected, not yet implemented) |

Claude Code reads `skill/SKILL.md`, enhances your plain-English request into a
provider-optimized prompt (additive craft only — never content moderation),
runs one command, and the router walks your provider list until one succeeds,
saves the PNG(s), and reports a machine-readable JSON result with both your
original and the enhanced prompt.

Requires Python 3.10+ and nothing else — no pip packages, no venv.

## Install (local machine, ~1 minute)

```
git clone <this repo> && cd <repo>
python install.py        # or: py -3 install.py / python3 install.py / ./install.sh
```

This copies `skill/` to `~/.claude/skills/imagegen/`, creates a commented
config template at `~/.config/imagegen/env` (never overwrites an existing
one), and creates `~/Pictures/imagegen`. Re-running after `git pull` is safe;
`python install.py --uninstall` removes the skill and keeps config/outputs.

Then edit `~/.config/imagegen/env` and add at least one API key.

## Verify

```
python ~/.claude/skills/imagegen/scripts/imagegen.py status
```

Live smoke tests (run the ones whose provider you configured):

```
python ~/.claude/skills/imagegen/scripts/imagegen.py generate "a red cube on a white background" --provider gemini --json
python ~/.claude/skills/imagegen/scripts/imagegen.py generate "a red cube on a white background" --provider openai --json
python ~/.claude/skills/imagegen/scripts/imagegen.py generate "a red cube on a white background" --provider grok --json
```

On Windows, substitute `py -3` (or `python`) for `python3` anywhere; all
paths and scripts are cross-platform.

## Usage

```
imagegen.py generate PROMPT [--provider auto|gemini|openai|grok] [--tier 1|2]
                     [--n 1..4] [--size WxH] [--seed N] [--model ID]
                     [--out PATH] [--ref IMG]... [--source "original ask"]
                     [--timeout SECS] [--json] [--dry-run]
imagegen.py status [--json]
```

Exit codes: `0` success, `2` usage/Phase-2 surface, `3` not configured,
`4` all providers failed, `5` content rejected by every attempted provider.
The final stdout line is always a single JSON document.

## Tests

```
python tests/acceptance.py     # offline suite (A1-A3, A6-A11); no keys, no network
```

Live smokes (A4/A5) are intentionally not run in CI/cloud — keys stay on
your machine (PDR §15.4).

## Repo layout

- `skill/` — exact content installed to `~/.claude/skills/imagegen/`
  (`SKILL.md`, `scripts/imagegen.py`, `references/prompting.md`,
  `references/providers.md`)
- `install.py` — canonical cross-platform installer (`install.sh` = thin wrapper)
- `tests/acceptance.py` — canonical offline acceptance runner (`.sh` = wrapper)

Phase 2 (roadmapped, not built): ComfyUI adapter, upscaling, parallel
fan-out, MCP promotion. All CLI names, env vars, and code seams for these are
reserved.
