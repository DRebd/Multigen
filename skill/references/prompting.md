# Prompt craft guide (per provider)

How to expand a user's plain-English sentence into what each generator
responds to best. Enhancement is **strictly additive craft** — composition,
lighting, style, medium, mood, plausible detail — never removal, softening,
or moderation of anything the user asked for. When in doubt, send the user's
words verbatim.

## Gemini (`gemini-2.5-flash-image`, "Nano Banana" lineage)

Style: **flowing narrative description, scene-first.** Write one or two rich
prose sentences, as if describing a finished photograph or painting. Name the
medium and camera/style language explicitly. Gemini is best-in-class at
rendering text inside images — quote any desired in-image text exactly, in
double quotes. Aspect ratio comes from `--size` (mapped to the nearest of
21:9, 16:9, 4:3, 3:2, 5:4, 1:1, 4:5, 3:4, 2:3, 9:16).

- User: *"a lighthouse in a storm"* →
  "A weathered stone lighthouse on a jagged headland battered by a night
  storm, enormous waves exploding against the rocks, its beam cutting through
  driving rain; dramatic low-angle wide shot, cinematic lighting, dark teal
  and slate palette, photorealistic, shot on a full-frame camera at 24mm."
- User: *"logo-ish banner that says GRAND OPENING"* →
  "A festive storefront banner design with the words \"GRAND OPENING\" in
  bold gold serif lettering on deep red fabric with subtle drapes and
  confetti, front-on composition, studio lighting, crisp vector-clean edges."
- User: *"a fox reading a book"* →
  "A red fox curled in a leather armchair reading a small clothbound book by
  warm lamplight in a cozy wood-paneled study, soft depth of field, gentle
  storybook watercolor illustration, muted autumn palette."

## OpenAI GPT Image (`gpt-image-2`)

Style: **clear scene statement + enumerated style descriptors.** Lead with
one plain sentence of subject and layout, then append named artistic mediums,
palette, and layout instructions as short clauses. Responds well to explicit
composition directions ("centered", "rule of thirds", "isometric view",
"flat vector illustration", "white background").

- User: *"an icon of a rocket"* →
  "A minimal app icon of a stylized rocket launching, centered composition,
  flat vector illustration, rounded geometry, two-tone indigo and coral
  palette, subtle long shadow, solid white background."
- User: *"team page hero image, people collaborating"* →
  "A diverse team collaborating around a table with laptops and sticky notes
  in a bright modern office, wide 16:9 hero composition with copy space on
  the left, soft natural window light, warm editorial photography style."
- User: *"a cross-section of a beehive"* →
  "An educational cutaway diagram of a beehive showing hexagonal comb
  chambers, brood cells, and worker bees, isometric view, detailed scientific
  illustration, labeled-textbook aesthetic, warm amber palette."

## Grok (xAI Grok Imagine, Flux lineage — `grok-imagine-image`)

Style: **photographic parameters and tight comma-separated descriptors.**
Concrete adjectives beat abstractions; specify lens, lighting setup, film
stock or render style. Keep phrases short and stacked. (`--size` is not
supported by this API in v1 — the provider default resolution is used.)

- User: *"portrait of an old fisherman"* →
  "Weathered elderly fisherman portrait, salt-and-pepper beard, yellow
  raincoat, harbor at dawn behind him, 85mm lens, f/1.8 shallow depth of
  field, overcast softbox-like light, Kodak Portra 400 film look, fine skin
  texture, photorealistic."
- User: *"cyberpunk street at night"* →
  "Rain-slick neon cyberpunk alley at night, holographic signage, steam from
  vents, lone figure with umbrella, 35mm lens, low-angle shot, cyan-magenta
  neon rim lighting, wet-surface reflections, cinematic, ultra-detailed."
- User: *"a cozy cabin in winter"* →
  "Snow-covered log cabin at blue hour, warm amber windows, smoke from
  chimney, pine forest, fresh snowfall, 50mm lens, tripod long exposure,
  soft ambient twilight, crisp winter air clarity, photorealistic landscape."

## (Phase 2) ComfyUI / SD-family

Reserved. When the ComfyUI adapter ships: tag-style prompts, quality tags
(e.g. "masterpiece, best quality"), and negative-prompt conventions via
`--negative`. Do not use tag-style prompting with the v1 providers above.

## Auto mode

When `--provider auto`, craft for the **first configured provider in the tier
order** (default: gemini). The result JSON's `provider` field reveals if a
different provider actually served the request after fallback.
