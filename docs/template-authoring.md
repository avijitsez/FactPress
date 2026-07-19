# Template authoring guide

This is the guide for writing a new FactPress template — either upstreaming
one into `templates/` or building a private one under `template_paths`. Start
here; for the exact fields the renderer hands your Jinja template, see
[rendering-contract.md](rendering-contract.md) — this doc is the surrounding
workflow, that one is the binding interface.

## Anatomy of a template

Every template is a directory with three files:

```
templates/<template_id>/
├── manifest.yaml       # what the director LLM is allowed to choose
├── template.svg.j2      # the layout, in Jinja2-templated SVG
└── preview.png           # a rendered sample, feed size, committed
```

`template_id` is the directory name and must match `manifest.yaml`'s `id`.

## The manifest

`manifest.yaml` is the catalog entry the director LLM sees, and the schema
the engine validates the template against (`load_manifest` in
`factpress/renderer/engine_svg.py`). Required keys:

```yaml
id: daily_pnl              # matches the directory name
version: 1.0.0              # semver; a DesignSpec pins the version it was
                             #   authored against (template_version)
name: "Daily P&L"           # human-readable, shown to the director
variants:                   # at least one; DesignSpec.variant must be one of these
  - default

sizes:                      # both exports the template must lay out sensibly at
  feed: [1080, 1350]
  telegram: [1280, 720]

slots:                      # character caps for LLM-authored copy fields
  headline:
    max: 60
  subhead:
    max: 90
  emoji:
    max: 4

palettes_allowed:           # subset of the brandkit's palette names
  - midnight
  - ember
  - aurora

sparkline: optional         # required | optional | none
states:
  - static                  # state layers beyond "static" are a v0.2 feature (see below)
```

`load_manifest` enforces: `id` is a non-empty string, `version` is valid
semver, `sizes` and `palettes_allowed` are non-empty. Keep slot caps
realistic for your layout — the renderer does not re-check the director's
output against a manifest at render time; caps are a *design* constraint the
director is told about, not a runtime enforcement point.

## The render context contract

Templates receive a fully-resolved context — `W`, `H`, `size`, `spec`,
`brand`, `view` — built by the engine from facts + spec + brandkit. Every
numeral in `view` is **already formatted**: signs, currency symbols, percent
signs, thousands separators, delta arrows. Templates lay things out; they
never format a number. Read the full field-by-field contract in
[rendering-contract.md](rendering-contract.md) before writing a template —
it documents `view.hero`, `view.delta_chips`, `view.callouts`,
`view.sparkline`, and the formatting heuristics in `format.py` that produce
them.

Two rules worth restating here because new template authors trip on them:

- Parametrize by `W`/`H`. A template must render sensibly at both
  1080×1350 (feed) and 1280×720 (telegram) from the same `.svg.j2` — no
  hardcoded canvas size, no size-specific template files.
- `view.emoji` is always `None` in the image. Vendored fonts carry no emoji
  glyphs; `spec.emoji` reaches the end user only in the Telegram caption
  (added by the publisher). Guard it with `{% if %}` if your layout checks
  it, but do not expect it rendered.

## Slots, caps, and palettes

- **Slots** (`headline`, `subhead`, `emoji`) are the LLM-authored copy
  fields, each with a `max` character cap in the manifest. The schema-level
  hard digit ban (see `factpress/schemas.py`) already rejects any Unicode
  numeral in these fields regardless of manifest caps — caps are about
  length and layout, the digit ban is about number-hallucination safety.
- **Palettes** are named color sets defined once in the brandkit
  (`brandkits/default.yaml`: `midnight`, `ember`, `aurora`, ...) and
  referenced by name in `palettes_allowed`. A template only lists which
  palettes it supports; the actual color values (`bg`, `fg`, `accent`,
  `positive`, `negative`, `grad_from`, `grad_to`, `chip_bg`, ...) come from
  the brandkit at render time via `brand.palette`. Add a palette by editing
  the brandkit, not the template.

## State layers (coming in v0.2 / F5)

`states: [static]` is the only state every current template supports.
Interactive approval cards (F5) will add state layers — `pending`,
`approved`, `rejected`, `executed`, `expired` — as enumerated,
deterministically-rendered visual layers (ribbons, stamps, dimming, button
layout) that the director LLM has zero control over. That work has not
landed yet; today's templates only ever render `static`. If you're
authoring a template now, you don't need to plan for state layers, but
avoid designs that would be awkward to add a stamp/ribbon overlay to later.

## Golden-test workflow for a new template

FactPress proves determinism with pixel-hash golden tests
(`tests/golden/test_golden.py`), keyed per-platform in
`tests/golden/hashes.json`. Adding a template (built-in or private) that you
want golden-covered:

1. Add a fixture: `tests/golden/fixtures/<name>.json` with `{"facts": {...},
   "spec": {...}}` — a valid facts payload for your event type and a
   `DesignSpec` with `template_id` pointing at your template.
2. Generate hashes for your platform:
   ```bash
   pytest tests/golden --update-golden
   ```
   This re-renders every fixture × size (`feed`, `telegram`) for the current
   platform and rewrites `hashes.json`. Review the diff — it should only add
   entries for your new fixture (or intentionally change existing ones if
   you changed a template on purpose).
3. Run the full suite normally (`pytest tests/ -q`) to confirm the new
   golden entries pass without `--update-golden`.
4. Commit the fixture and the `hashes.json` diff together, with a message
   explaining what changed and why (see CONTRIBUTING.md's rules on
   regenerating golden hashes — this is a reviewed, deliberate action, never
   a side effect of an unrelated change).

Every platform in CI populates its own `hashes.json` entries the first time
a fixture runs there; a platform with no stored hash for a given fixture ×
size skips that comparison rather than failing.

## Private templates via `template_paths`

You don't have to upstream a template to use it. `FactPress(template_paths=
["./templates_private"])` searches your directories *before* the built-ins,
so a private template can either add a new `template_id` or override a
built-in one entirely, without forking FactPress:

```python
from factpress import FactPress

ff = FactPress(template_paths=["./templates_private"])
png = ff.render(facts={...}, event_type="my_custom_event")
```

The litmus test for whether a template belongs upstream instead: would a
stranger using FactPress for a non-trading app find it useful as-is with
their own facts? If it encodes semantics specific to your host system
(specific event routing, brand-specific copy, etc.), keep it in your own
`template_paths` directory.
