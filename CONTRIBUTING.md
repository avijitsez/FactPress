# Contributing to FactPress

Thanks for looking at FactPress. This document covers dev setup, the test/lint
loop, and — most importantly — the rules around golden-hash regeneration and
determinism, since those are the properties this project exists to guarantee.

## Dev setup

```bash
git clone https://github.com/avijitsez/FactPress.git
cd FactPress
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -e ".[dev]"
```

`[dev]` pulls in `pytest`, `ruff`, `build`, and `twine`. The optional
`[html]` extra (Playwright) is only needed if you're working on the
alternate HTML/CSS rendering engine — not required for the default
SVG/resvg path.

## Tests and lint

```bash
pytest tests/ -q
ruff check .
```

Both must be clean before opening a PR. CI (`.github/workflows/ci.yml`) runs
both on Python 3.11 and 3.13.

Golden-image tests live in `tests/golden/` and hash-compare every fixture ×
size combination against `tests/golden/hashes.json`. They're part of the
normal `pytest` run — see the rules below before touching that file.

## Golden-hash regeneration rules

`tests/golden/hashes.json` is the proof that "same facts + same DesignSpec
⇒ byte-identical PNG" actually holds. Treat it as generated evidence, not a
config file to hand-edit:

- **Only regenerate with `--update-golden`:**
  ```bash
  pytest tests/golden --update-golden
  ```
  Never hand-edit `hashes.json`.
- **Only regenerate when you mean to.** A diff in `hashes.json` should be
  either (a) new entries for a new fixture/template you added, or (b)
  changed entries because you intentionally changed a template's rendered
  output. If you did neither and `hashes.json` changed, something in the
  renderer, a font, or the `resvg-py` version moved — investigate before
  committing, don't just re-run `--update-golden` to make it pass.
  - This applies to this PR's own hygiene too: if you only touched docs or
    template `preview.png` files, `git diff tests/golden/hashes.json`
    should be empty. If it isn't, stop and find out why.
- **Review the diff like a code change.** Read which keys changed
  (`<fixture>@<size>` under the platform's key) before committing, and say
  why in the PR description.
- **Hashes are per-platform** (keyed by `sys.platform`). Don't try to make
  your local platform's hashes match another platform's — a platform with
  no stored entry for a fixture × size simply skips that comparison; CI
  populates its own entries independently.

## Determinism rules

These are non-negotiable for anything touching the renderer or templates:

- **No `locale` stdlib module, anywhere.** OS-dependent formatting is exactly
  the nondeterminism this project is built to avoid. Month abbreviations
  etc. are hardcoded in English in `factpress/renderer/format.py`.
- **Fonts are vendored only** (`factpress/assets/fonts/`), and rendering
  runs with `skip_system_fonts=True`. Don't add a font dependency that isn't
  vendored into that directory.
- **LF line endings** for anything the golden tests or hashing touch —
  `hashes.json` is written with `write_bytes` specifically to avoid
  Windows text-mode newline translation. If you're editing generated JSON
  by hand for any reason (you generally shouldn't), preserve LF endings.
- **All float coordinates in SVG paths are fixed to 2dp** — don't introduce
  a new path-generation helper with platform-dependent float formatting.
- **The `resvg-py` version is pinned** in `pyproject.toml`
  (`resvg-py==0.3.3`). Bumping it is a deliberate, reviewed change that
  requires regenerating golden hashes for every platform — flag it clearly
  in the PR description, don't bundle it with unrelated work.

## PR expectations

- Keep PRs scoped to one logical change. Docs, a new template, and a
  renderer change are three PRs, not one.
- Include the exact commands you ran and their pass/fail result (test +
  lint at minimum) in the PR description — not "tests pass," the actual
  command and outcome.
- If your change touches `tests/golden/hashes.json`, explain why in the PR
  description (new fixture vs. intentional visual change) per the rules
  above.
- New templates: see
  [docs/template-authoring.md](docs/template-authoring.md) for the manifest
  schema and the golden-test workflow for adding one, whether upstreaming a
  built-in template or documenting a private `template_paths` template.
- Match existing code style and structure rather than introducing a new
  pattern for something the codebase already does a certain way.
