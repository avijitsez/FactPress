# FactPress — LLM-Art-Directed, Deterministically-Rendered Infographic Notifications
**New standalone repo:** `github.com/avijitsez/factpress` (name verified clean on GitHub/PyPI/trademark)
**Tagline:** facts in, identical prints out — the LLM is the editor, never the printer.
**Licence:** Apache-2.0 (matches your stack's licence posture; permissive for OSS adoption)
**Relationship to AI-Trading:** zero trading logic inside FactPress. AI-Trading consumes it
as a pip dependency via a thin bridge module. Anyone else can plug in their own facts.

---

## 1. Core principle: three-stage separation with numbers-by-reference

```
[ANY SYSTEM]                [FACTFRAME]
 produces facts   ──JSON──►  1. FACT VALIDATION (Pydantic schema per event type)
 (your trading               2. CREATIVE DIRECTOR (LLM)
  system, or                    in : facts + template catalog + brand kit + tone
  anyone's app)                 out: DesignSpec JSON — template choice, headline,
                                     emphasis keys, palette pick, layout variant,
                                     caption. STRICTLY validated. Retried once,
                                     then falls back to default copy.
                             3. RENDERER (deterministic, zero LLM)
                                Jinja2 SVG template + facts + spec → PNG
                                ALL numbers formatted by the renderer
                                directly from the facts payload
                             4. PUBLISHER → Telegram sendPhoto
                                (chat_id + message_thread_id aware)
```

**The guardrail that makes this safe for finance:** the DesignSpec schema has
no free numeric fields. The LLM references metrics **by key** ("feature
`daily_pnl_pct` as the hero stat"), and the renderer resolves the key against
the facts payload with locale/precision formatting. A hallucinated number is
structurally impossible — worst case is a badly chosen headline, never a wrong
figure.

## 2. What "creative freedom" means concretely

The LLM controls (within enums/caps): which template variant, hero metric
selection, headline + subhead copy (length-capped per slot), tone (celebratory
/ neutral / cautionary — auto-constrained: a red day cannot use celebratory),
palette choice from the brand kit's allowed set, emoji slots, sparkline
on/off, callout ordering, caption text for the Telegram message.

The renderer controls (LLM cannot touch): every numeral, currency symbols,
+/- signs and delta arrows, sparkline drawn from the facts' series array,
timestamps, the compliance footer ("Automated summary — not investment
advice"), logo/watermark, safe-area layout.

**"Viral modern" is a template-pack property, not an LLM property:** dark-mode
first, oversized hero number, gradient accents, delta chips, sparklines,
1080×1350 (feeds) and 1280×720 (Telegram-optimal) exports. Ship one strong
default brand kit; users override fonts/colors/logo via `brandkit.yaml`.

## 3. Repo layout

```
factpress/
├── factpress/
│   ├── schemas.py            # FactPayload base + per-event schemas; DesignSpec
│   ├── director.py           # OpenAI-compatible client, spec validation,
│   │                         #   1 retry → deterministic fallback copy
│   ├── renderer/
│   │   ├── engine_svg.py     # Jinja2 SVG → PNG via resvg-py (default, no browser)
│   │   ├── engine_html.py    # optional extra: HTML/CSS via Playwright
│   │   ├── sparkline.py      # deterministic series → path
│   │   └── format.py         # locale, precision, currency, delta chips
│   ├── publisher.py          # Telegram (token, chat_id, thread_id, silent hours)
│   ├── pipeline.py           # publish(facts: dict, event_type: str) -> MessageId
│   └── cli.py                # factpress render sample.json --preview
├── templates/                # each: template.svg.j2 + manifest.yaml + preview.png
│   ├── daily_pnl/            # manifest declares slots, caps, allowed variants —
│   ├── trade_executed/       #   this catalog is what the director LLM sees
│   │                         #   variants: buy_opened | position_closed
│   ├── pulse_update/         # compact recurring status card (hourly lanes)
│   ├── session_digest/       # variants: session_open | session_close
│   ├── digest_top_picks/
│   ├── milestone/
│   └── reflection_recap/     # weekly recap + prose reflection slot
├── brandkits/default.yaml    # palettes, fonts, logo slot, watermark
├── tests/
│   ├── golden/               # pixel-hash golden-image tests (determinism proof)
│   └── test_director_fallback.py, test_numbers_by_reference.py, ...
├── examples/                 # runnable fact payloads, no trading assumptions
└── pyproject.toml            # Apache-2.0; deps: pydantic, jinja2, resvg-py,
                              #   httpx; extras: [html] playwright
```

**Public API (the whole plug-n-play surface):**
```python
from factpress import FactPress
ff = FactPress(
    llm_base_url="http://localhost:8100/v1",   # any OpenAI-compatible endpoint
    llm_model="whatever-your-relay-serves",
    telegram_token=..., default_chat_id=...,
    brandkit="brandkits/default.yaml",
    template_paths=["./templates_private"],    # searched before built-ins —
)                                              #   host-specific templates
                                               #   without forking; upstream
                                               #   generic ones later
ff.publish(facts={...}, event_type="trade_closed",
           chat_id=..., thread_id=...)          # topic-aware
```

## 4. Determinism & OSS quality bar
- Same facts + same DesignSpec ⇒ byte-identical PNG (golden tests in CI)
- Director is the ONLY nondeterministic stage, and it's quarantined behind a
  validated schema with a deterministic fallback — notifications never block
  on a bad LLM day
- No secrets, no telemetry, semantic versioning of templates (a spec pins the
  template version it was directed against)

## 5. Integration with AI-Trading (bridge lives in YOUR repo, not FactPress)

`AI-Trading/notifications/factpress_bridge.py`:
- Maps events → fact payloads, each with its own builder and schedule:
  · hourly pulse (aggressive lanes only): mode P&L, open positions, orders
    vs cap — cron :00 during that broker's market hours → mode topic
  · buy/sell fills (neutral + conservative lanes only): per-fill
    trade_executed card from position_manager → mode topic
  · session_open / session_close digests: orchestrator session hooks,
    per broker → General topics
  · scout digest top picks, milestone/streaks (signal_quality_tracker)
  · weekly reflection_recap: numbers from signal_quality_tracker; the
    prose reflection arrives IN THE FACTS from TA reflection memory /
    Phase-11 evolver — insights-by-reference: the director may select
    and trim reflection text, never author it
  Which lanes get which cards is bridge routing config, not FactPress logic
- **PII scrub runs before payload construction** (facts are already
  account-number-free by design — symbols, percentages, counts only)
- LLM endpoint = your relay :8100 (inherits NIM → Hermes → Ollama fallback
  and backend logging for free)
- Publisher targets: Bot 2/3 General topics for digests, mode topics for
  per-book cards. Bot 1 policy revised: health alerts stay text-only, but
  approval requests become interactive cards (section 7) — approvals are
  Bot 1's core purpose, and a proposal card with buttons is higher-signal
  than a text wall, not lower
- Env: FACTFRAME_* keys in .env; new deps pinned in requirements.txt

## 6. Build phases

| Phase | Scope | Exit criteria |
|---|---|---|
| F0 | Repo scaffold, schemas, SVG engine, ONE template (daily_pnl), golden tests, CLI preview | `factpress render examples/daily_pnl.json` produces pixel-stable PNG |
| F1 | Director + spec validation + fallback; numbers-by-reference enforcement tests | Injected "hallucinate 999%" prompt attack cannot alter any numeral; fallback fires on invalid spec |
| F2 | Publisher (topics, silent hours), brand kits, second template | Card lands in a test Telegram topic with caption |
| F3 | Full 7-template pack (incl. pulse_update, session_digest, reflection_recap), template_paths search, docs, examples, CI, PyPI packaging | `pip install factpress` works clean; a private-path template overrides a built-in in tests; README with GIFs |
| F5 | Interactive approval channel (section 7): state layer, pending store, callback handler + optional poller, two-stage ack | Golden tests for every state stamp; double-tap and unauthorized-user tests; expiry renders EXPIRED and fires default action |
| F4 | (in AI-Trading repo) bridge module + event wiring + relay config + visual gate integration | Trade close ⇒ infographic in the right mode topic; gate approval via card, decision + execution stamps land in audit log |

F5 sits before F4 deliberately — the bridge should integrate against the
finished interactive API.

Build-agent model: same as INTEGRATION_PLAN_v3 — Fable 5 orchestrates,
Sonnet subagents write Python, Codex not needed (no frontend). F0–F3 happen
in the new repo; F4 is a small PR to AI-Trading.

## 7. Interactive Approval Channel (optional module)

Turns a published card into a decision surface: inline buttons on the image,
authorized-user taps, and **visual system acknowledgement** — the card itself
re-renders to show what was decided and what the system then did.

### Lifecycle & two-stage acknowledgement

```
PENDING ──tap──► DECIDED ──host confirms──► EXECUTED ✓
   │              (APPROVED/REJECTED         or FAILED ✗
   │               + who + when stamp,
   │               buttons removed)
   └──timeout──► EXPIRED (default action fires, stamp says so)
```

1. `publish_interactive()` renders the card with a PENDING ribbon + inline
   keyboard (e.g. Approve / Reject), stores a **single-use decision token**
   in a small pending-approvals SQLite table (Telegram callback_data is
   64-byte-capped, so buttons carry only the token).
2. On tap: instant `answerCallbackQuery` toast ("Got it — applying…"), token
   consumed (double-taps and races are no-ops), authorization checked against
   an allowlist of user IDs — unauthorized taps get a refusal toast and are
   logged, card unchanged.
3. **First visual ack:** same template re-rendered with the decision state
   layer (APPROVED/REJECTED stamp, decider, timestamp), pushed via
   `editMessageMedia`; keyboard removed. The host's `on_decision` callback
   fires with the decision.
4. **Second visual ack (the honest one):** when the host system finishes
   acting (order placed / rejected downstream by hard limits / failed), it
   calls `ff.update_state(message_ref, state="executed", facts_patch={...})`
   and the card re-renders once more — e.g. EXECUTED ✓ with the fill price
   patched into the facts. The card ends up reflecting reality, not the tap.
5. Timeout: configurable `timeout_s` + `default_action` (for trading:
   reject). Expiry re-renders with an EXPIRED stamp so a stale card can never
   be mistaken for a live decision.

### Determinism boundary (unchanged in spirit)
State visuals — ribbons, stamps, dimming, button layout — are **template
state layers**, enumerated in each template's manifest and rendered
deterministically. The director LLM art-directs the *proposal content* (why
this trade, which metrics to feature) exactly as in one-way mode; it has zero
control over states, buttons, or acknowledgement visuals. Golden tests cover
every state of every interactive template.

### Integration surface

```python
ref = ff.publish_interactive(
    facts=proposal_facts, event_type="trade_proposal",
    actions=[("approve", "Approve"), ("reject", "Reject")],
    authorized_users=[MY_USER_ID],
    timeout_s=60, default_action="reject",
    on_decision=gate_callback,            # host hook
    chat_id=BOT1_CHAT_ID,
)
# later, from the execution path:
ff.update_state(ref, state="executed", facts_patch={"fill_price": 2841.5})
```

Because FactPress is a library, not a daemon, callback delivery is pluggable:
- `ff.handle_callback(update)` — call it from an existing python-telegram-bot
  dispatcher (AI-Trading's concierge bots already run loops)
- `ff.run_poller()` — optional built-in long-poll loop for standalone users
  (this is what Bot 1 uses, since it has no loop today)

### Trust properties
Single-use tokens (idempotent taps) · user allowlist with logged refusals ·
default-deny on timeout · every decision emitted to the host with user id +
timestamp for the host's audit trail (in AI-Trading: `audit_logger` with
`source=factpress_gate`) · pending store survives restarts, and cards for
tokens invalidated by a restart re-render as EXPIRED rather than dangling.

### AI-Trading uses (behind config flags, all optional)
- **Human gate:** trade proposals on Bot 1 become cards — Kronos confidence,
  risk-team verdict, mode, trade plan — with Approve/Reject; EXECUTED stamp
  carries the fill. The existing text gate remains as fallback.
- **Concierge watchlist confirms** (Bots 2/3): the Confirm/Cancel flow from
  8-C gains the same visual ack pattern.
- **Kill-switch confirmation:** `/halt` asks for a confirm card; the HALTED
  stamp doubles as the visual record of who stopped the system and when.

## 8. Template pack & the OSS/host boundary

**Litmus test for where a template lives:** would a stranger using FactPress
for a non-trading app find it useful as-is with their own facts? Yes → OSS
pack. Encodes host-specific semantics → host repo via `template_paths`.

### Built-in archetypes (OSS)

| Archetype | Variants | Intended cadence | Notes |
|---|---|---|---|
| `daily_pnl` | default | daily | Hero metric + delta + sparkline |
| `trade_executed` | `buy_opened`, `position_closed` | per event | Entry/exit, plan vs outcome |
| `pulse_update` | default | recurring (hourly) | Compact status: 3-4 metrics, tiny sparkline, "as of HH:MM" prominent — designed to be glanceable and muted without loss |
| `session_digest` | `session_open`, `session_close` | 2×/session | Open: watchlist, regime, plan-for-day slots. Close: realised P&L, hit/miss vs open card |
| `digest_top_picks` | default | daily | Ranked candidate list |
| `milestone` | default | event | Streaks, records |
| `reflection_recap` | default | weekly | Weekly numbers + one prose reflection slot (length-capped) |

### Insights-by-reference (extends numbers-by-reference)
`reflection_recap` is the only archetype whose key content is prose. Same
discipline applies: reflection text is produced by the HOST system and
arrives in the facts payload (`facts.reflection_text`, list of candidates
allowed). The director may choose one and trim to the slot cap; it cannot
author insights. A notification layer must never invent lessons the system
didn't learn. Enforced the same way: the DesignSpec references reflection
candidates by index, not free text.

### What stays in the host repo (AI-Trading examples)
Schedules and session hooks · fact payload builders · event→template→topic
routing config (e.g. "hourly pulse for aggressive lanes only") · lane accent
colors (brand-kit override) · any experimental template in
`templates_private/` awaiting upstreaming.
