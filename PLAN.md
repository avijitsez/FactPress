# FactPress Build Plan — Orchestrator Edition

**Source spec:** [FACTPRESS_DESIGN.md](FACTPRESS_DESIGN.md)
**Target repo:** https://github.com/avijitsez/FactPress (currently LICENSE only, branch `main`)
**Build model:** Fable 5 orchestrates; Sonnet subagents write Python. No frontend → no Codex.
**Phase order:** F0 → F1 → F2 → F3 → F5. F4 (AI-Trading bridge) is a separate PR in the AI-Trading repo, out of scope for this plan except for its interface contract.

---

## 0. Assumptions — CONFIRMED by user 2026-07-18

Decisions locked:
1. **FactPress everywhere** — package `factpress`, env prefix `FACTPRESS_*`; the spec's `FACTFRAME` mentions are superseded.
2. **Hard digit ban** — LLM-authored copy slots (headline, subhead, caption) reject any `\d` at schema validation; all numerals come from renderer slots.
3. **Live-test LLM = NIM credentials from AI-Trading** — `NVIDIA_API_KEY` in `C:\AI-Trading\.env`, base URL `https://integrate.api.nvidia.com/v1` (or local relay `http://127.0.0.1:8100/v1` when running). Used for F1 live smoke only; unit tests mock the client.

| # | Assumption | Why it matters |
|---|---|---|
| A1 | ~~Naming~~ **CONFIRMED**: FactPress everywhere, env prefix `FACTPRESS_*`. | Env var contract for the F4 bridge |
| A2 | **Fonts are vendored** in `factpress/assets/fonts/` (OFL-licensed, e.g. Inter + JetBrains Mono) and resvg is configured to use *only* those fonts. | "Byte-identical PNG" is impossible with system font fallback; this is the linchpin of the golden tests |
| A3 | **Python ≥3.11**, deps pinned: `pydantic>=2`, `jinja2`, `resvg-py` (exact-pinned — rendering output may change between resvg versions), `httpx`. Extras: `[html]` playwright, `[interactive]` none extra (SQLite is stdlib). | Golden-test stability in CI |
| A4 | Golden tests compare **SHA-256 of PNG bytes**, regenerated via a `--update-golden` flag; CI runs on Linux (ubuntu-latest). If resvg output differs across OS, goldens are Linux-canonical and local Windows runs compare structural SVG output instead. | Dev machine is Windows; CI is the determinism referee |
| A5 | Director client is OpenAI-compatible chat completions via `httpx` directly (no `openai` SDK dependency) with `response_format`/JSON-mode when available, plain prompt otherwise. | Keeps dep tree tiny per spec's OSS posture |
| A6 | F2 Telegram verification uses a real test bot/chat provided via env at run time; unit tests mock `httpx`. | Exit criterion "card lands in a test topic" needs credentials only the user has |

---

## 1. Orchestration rules

- **One phase = one milestone branch** (`f0-scaffold`, `f1-director`, …) merged to `main` at the phase gate after the full test suite passes.
- **Subagents get explicit file ownership.** Two agents never touch the same file in the same wave. Contracts-first: `schemas.py` and template manifests are written and merged before dependent work fans out.
- **Every task below is atomic:** one logical change + its test + a verification command. Orchestrator runs the verification itself before accepting a subagent's work — subagent claims are not trusted.
- **Phase gates** = the spec's exit criteria, executed by the orchestrator in a clean venv.
- Subagent output contract: ≤30-line summary + file list + test command results. No transcripts.

### Subagent roster

| Agent | Model | Scope |
|---|---|---|
| `py-core` | Sonnet | schemas, renderer, formatters, pipeline |
| `py-director` | Sonnet | director, prompt, fallback, adversarial tests |
| `py-io` | Sonnet | publisher, CLI, interactive/callback layer |
| `templates` | Sonnet | SVG/Jinja2 templates + manifests + brand kits |
| `reviewer` | Sonnet, read-only (Read/Grep/Glob) | diff review at each phase gate |
| `haiku-testrunner` | Haiku | run suites, report pass/fail + failing names |

---

## 2. Phase F0 — Scaffold, schemas, SVG engine, one template, golden tests, CLI

**Gate:** `factpress render examples/daily_pnl.json --preview` produces a pixel-stable PNG; golden test green twice in a row (determinism proof = render twice, hashes equal).

| ID | Task | Owner | Files | Verify |
|---|---|---|---|---|
| F0.1 | Git init in `C:\FactPress`, remote → GitHub, pull LICENSE, scaffold `pyproject.toml` (Apache-2.0, deps per A3), package skeleton, `README.md` stub, `.github/workflows/ci.yml` (pytest + ruff on 3.11/3.12) | orchestrator | repo root | `pip install -e .[dev]` clean; CI green on push |
| F0.2 | `schemas.py`: `FactPayload` base, `DailyPnlFacts`, `DesignSpec` (enums only — template_id, variant, tone, palette_id, hero_metric_key, headline/subhead with length caps, emphasis_keys, sparkline flag, caption; **zero free numeric fields**), spec-pins-template-version field | py-core | `factpress/schemas.py`, `tests/test_schemas.py` | `pytest tests/test_schemas.py` — includes test that a numeric literal in any copy-adjacent field fails validation where caps apply |
| F0.3 | `renderer/format.py`: locale/precision/currency, +/- signs, delta chips, timestamp formatting. Pure functions, table-driven tests | py-core | `renderer/format.py`, `tests/test_format.py` | `pytest` — covers negative, zero, tiny/huge magnitudes, currency symbols |
| F0.4 | `renderer/sparkline.py`: series → SVG path, deterministic, handles len 0/1/2, flat series, NaN rejection | py-core | `renderer/sparkline.py`, `tests/test_sparkline.py` | `pytest` — same input ⇒ same path string |
| F0.5 | `renderer/engine_svg.py`: manifest loader, Jinja2 env (StrictUndefined, autoescape for SVG), facts+spec → SVG → PNG via resvg with vendored fonts only; 1080×1350 and 1280×720 exports | py-core | `renderer/engine_svg.py`, `factpress/assets/fonts/` | `pytest tests/test_engine.py` — render twice, byte-equal |
| F0.6 | `daily_pnl` template: `template.svg.j2` + `manifest.yaml` (slots, caps, variants, palettes, semver) in the dark-mode "viral modern" style: oversized hero number, gradient accent, delta chip, sparkline, compliance footer, safe areas | templates | `templates/daily_pnl/*`, `brandkits/default.yaml` (minimal) | renders green + red day examples without StrictUndefined errors |
| F0.7 | Golden test harness: `tests/golden/` with hash manifest, `--update-golden` flag, red-day + green-day + sparkline-off fixtures | py-core | `tests/golden/*`, `tests/conftest.py` | `pytest tests/golden` green twice consecutively |
| F0.8 | `cli.py`: `factpress render <facts.json> [--template] [--out] [--preview]` (no LLM — uses deterministic fallback spec), entry point in pyproject | py-io | `factpress/cli.py`, `tests/test_cli.py` | **gate command** from spec runs end-to-end |
| F0.9 | Phase gate: reviewer subagent diff review, full suite in clean venv, merge to `main` | orchestrator + reviewer | — | CI green on `main` |

Wave plan: F0.1 solo → F0.2 solo (contract) → F0.3/F0.4/F0.6 in parallel → F0.5 → F0.7/F0.8 in parallel → F0.9.

---

## 3. Phase F1 — Director, spec validation, fallback, numbers-by-reference enforcement

**Gate:** injected "hallucinate 999%" prompt attack cannot alter any numeral in output PNG; invalid spec triggers deterministic fallback (1 retry → fallback).

| ID | Task | Owner | Files | Verify |
|---|---|---|---|---|
| F1.1 | Template **catalog builder**: manifests → compact JSON catalog the director sees (slots, caps, variants, palette ids) | py-core | `factpress/catalog.py`, tests | `pytest` |
| F1.2 | `director.py`: httpx OpenAI-compatible client, system prompt (facts + catalog + brand kit + tone rules), strict `DesignSpec.model_validate` on response, **1 retry with validation errors echoed, then `fallback_spec(facts, event_type)`** — deterministic default copy per template | py-director | `factpress/director.py`, `tests/test_director_fallback.py` | mocked-LLM tests: valid / invalid-then-valid / invalid-twice⇒fallback / timeout⇒fallback / non-JSON⇒fallback |
| F1.3 | Tone auto-constraint: red day (sign of hero metric / explicit facts field) forbids `celebratory`; enforced in **validation**, not prompt | py-director | `schemas.py` (validator), tests | `pytest` — celebratory+negative-pnl spec rejected |
| F1.4 | Numbers-by-reference enforcement tests: adversarial suite — LLM mock returns specs with digits smuggled into headline/caption beyond policy, unknown metric keys, oversized copy; renderer/schema must reject or strip. Property test: every numeral in output SVG text nodes must be traceable to a `format.py` call on a facts key | py-director | `tests/test_numbers_by_reference.py` | **gate test**: mock director instructed to output "999%" → rendered PNG hash equals fallback-copy render for same facts, or spec rejected |
| F1.5 | `pipeline.py` (partial): `render(facts, event_type) -> PNG bytes` wiring validation → director → renderer; director failure never blocks (fallback path) | py-core | `factpress/pipeline.py`, tests | `pytest` |
| F1.6 | Phase gate: reviewer pass | orchestrator | — | full suite + CI |

**Digit policy (DECIDED):** hard ban — `DesignSpec` validators reject any `\d` in LLM-authored copy slots (headline, subhead, caption). Implemented in F0.2's schema, adversarially tested in F1.4.

---

## 4. Phase F2 — Publisher, brand kits, second template

**Gate:** card lands in a test Telegram topic with caption (needs `FACTPRESS_TELEGRAM_TOKEN` + test chat from user).

| ID | Task | Owner | Files | Verify |
|---|---|---|---|---|
| F2.1 | `publisher.py`: Telegram `sendPhoto` via httpx — token, `chat_id`, `message_thread_id`, caption, silent-hours window (queue-or-silent policy: use `disable_notification` inside window), retry w/ backoff on 429/5xx | py-io | `factpress/publisher.py`, tests (mocked httpx) | `pytest` — thread_id propagation, silent-hours boundary cases, 429 retry |
| F2.2 | Brand kit system: `brandkits/default.yaml` full schema (palettes incl. allowed-set for director, fonts, logo slot, watermark), loader + validation, template access via Jinja context | templates + py-core (loader) | `factpress/brandkit.py`, `brandkits/default.yaml`, tests | golden tests re-baselined once; custom-kit override test |
| F2.3 | Second template: `trade_executed` with variants `buy_opened` / `position_closed` (entry/exit, plan vs outcome) + goldens for both variants | templates | `templates/trade_executed/*`, golden fixtures | `pytest tests/golden` |
| F2.4 | `pipeline.publish()` complete: `publish(facts, event_type, chat_id, thread_id) -> MessageId`; `FactPress` facade class per spec §3 public API | py-core | `factpress/__init__.py`, `pipeline.py`, tests | `pytest`; then **live gate**: one real send to test topic (user supplies creds) |
| F2.5 | Phase gate + reviewer pass | orchestrator | — | CI green |

---

## 5. Phase F3 — Full template pack, template_paths, docs, CI, PyPI

**Gate:** `pip install factpress` clean from a built wheel; private-path template overrides a built-in in tests; README with GIFs.

| ID | Task | Owner | Files | Verify |
|---|---|---|---|---|
| F3.1 | `template_paths` search order (user paths before built-ins), semver check, override test | py-core | `catalog.py`, `engine_svg.py`, tests | override test green (gate) |
| F3.2 | Templates wave A: `pulse_update` (compact, "as of HH:MM" prominent), `session_digest` (`session_open`/`session_close`) | templates | `templates/pulse_update/*`, `templates/session_digest/*`, goldens | goldens green |
| F3.3 | Templates wave B: `digest_top_picks` (ranked list), `milestone` (streaks/records) | templates (2nd instance, disjoint dirs) | `templates/digest_top_picks/*`, `templates/milestone/*`, goldens | goldens green |
| F3.4 | `reflection_recap` + **insights-by-reference**: `facts.reflection_text` candidate list; DesignSpec gains `reflection_index` + trim-to-cap; director cannot author prose — adversarial test mirrors F1.4 | py-director + templates | schema field, template, `tests/test_insights_by_reference.py` | adversarial test: mock director free-text reflection rejected |
| F3.5 | `examples/` payloads for all 7 event types (no trading assumptions beyond neutral finance), each renders via CLI | py-io | `examples/*.json` | loop: `factpress render examples/*.json` all succeed |
| F3.6 | Docs: README (quickstart, GIFs of each card, plug-n-play API, determinism story), per-template previews (`preview.png` committed), CONTRIBUTING, template-authoring guide | orchestrator + templates | README.md, docs/ | reviewer pass for accuracy |
| F3.7 | Packaging: wheel/sdist build, `pipx run twine check`, TestPyPI upload, clean-venv `pip install` + import + CLI smoke; release CI workflow (tag-triggered) | py-io | pyproject, `.github/workflows/release.yml` | **gate command** in a fresh venv |
| F3.8 | Phase gate: full suite, reviewer, tag `v0.1.0`, **PyPI publish only on explicit user confirmation** | orchestrator | — | CI green, wheel installs |

---

## 6. Phase F5 — Interactive approval channel

**Gate:** golden tests for every state stamp of every interactive template; double-tap and unauthorized-user tests; expiry renders EXPIRED and fires default action.

| ID | Task | Owner | Files | Verify |
|---|---|---|---|---|
| F5.1 | State-layer schema: manifests enumerate `states: [pending, approved, rejected, executed, failed, expired]` with ribbon/stamp/dim layers; renderer accepts `state` + stamp metadata (decider, timestamp) — deterministic, zero LLM control | py-core + templates | manifest schema, `engine_svg.py`, `trade_proposal` template with all 6 states | golden test per state (**gate**) |
| F5.2 | Pending store: SQLite table (token PK single-use, message ref, expiry, default_action, allowlist, facts snapshot, spec snapshot); survives restart; restart-invalidated tokens → EXPIRED re-render on startup sweep | py-io | `factpress/interactive/store.py`, tests | `pytest` — restart simulation test |
| F5.3 | `publish_interactive()`: render PENDING + inline keyboard (callback_data = token only, ≤64 bytes), store row, return `ref` | py-io | `factpress/interactive/api.py` | mocked-Telegram test |
| F5.4 | Callback handling: `handle_callback(update)` — instant `answerCallbackQuery` toast, atomic token consumption (double-tap/race = no-op, tested with threads), allowlist check (refusal toast + log, card unchanged), first visual ack via `editMessageMedia` (stamp + keyboard removed), `on_decision` fired | py-io | `interactive/callbacks.py`, tests | **gate tests**: double-tap, unauthorized |
| F5.5 | `update_state(ref, state=, facts_patch=)`: second visual ack — EXECUTED ✓ / FAILED ✗ with patched facts (e.g. fill price) re-rendered + `editMessageMedia` | py-io | `interactive/api.py`, tests | golden covers patched-facts render |
| F5.6 | Expiry: `timeout_s` + `default_action`; lazy check on tap + optional sweep in `run_poller()`; EXPIRED stamp render; default action fired exactly once | py-io | `interactive/expiry.py`, tests | expiry gate test |
| F5.7 | `run_poller()`: optional long-poll loop (getUpdates) for standalone users; clean shutdown | py-io | `interactive/poller.py`, tests | mocked long-poll test |
| F5.8 | Decision audit emission: every decision → host callback with user id + timestamp + token; docs for wiring to host audit logs | py-io | api + docs | `pytest` |
| F5.9 | Phase gate: reviewer, full suite, tag `v0.2.0` | orchestrator | — | CI green |

---

## 7. F4 interface contract (built here, consumed in AI-Trading)

Not built in this repo, but F0–F5 must ship exactly what the bridge needs:
`FactPress(...)` constructor per §3 · `publish()` / `publish_interactive()` / `update_state()` / `handle_callback()` / `run_poller()` · `template_paths` for `templates_private/` · `FACTPRESS_*` env conventions documented in README · decision events carry user id + timestamp for `audit_logger(source=factpress_gate)`.

---

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| resvg-py cross-platform pixel drift breaks "byte-identical" | Vendored fonts (A2), exact pin (A3), Linux-canonical goldens (A4); determinism claim scoped to pinned env |
| LLM JSON quality varies across OpenAI-compatible backends | Strict validation + retry-with-errors + deterministic fallback is the contract, not prompt quality; fallback path golden-tested |
| Telegram `editMessageMedia` rate limits during state churn | Publisher-level backoff; state renders coalesced (only latest state wins) |
| Template/spec version skew | DesignSpec pins template semver (F0.2); renderer rejects mismatched major |
| Golden re-baselining hides regressions | `--update-golden` requires explicit flag; reviewer must approve any golden diff at phase gates |

---

## 9. Execution readiness checklist (before F0.1)

- [x] User confirms assumptions A1–A6 — confirmed 2026-07-18 (FactPress naming everywhere)
- [x] User confirms F1 digit-policy question — hard ban on `\d` in LLM copy slots
- [ ] Git identity/push access to `avijitsez/FactPress` confirmed (`gh auth status`)
- [ ] Telegram test bot token + test chat/topic available by F2.4 (not needed earlier)
- [x] LLM endpoint for live director smoke test — NIM via `NVIDIA_API_KEY` in `C:\AI-Trading\.env`, `https://integrate.api.nvidia.com/v1`
