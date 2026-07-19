# Interactive approvals (F5, FACTPRESS_DESIGN.md §7)

The interactive channel turns a published card into a decision surface:
inline buttons on the image, authorized-user taps, and **visual system
acknowledgement** — the card itself re-renders to show what was decided and
what the system then did. This doc covers the actual API surface
(`factpress/interactive/api.py`, `factpress/interactive/poller.py`), how
callback delivery is wired up, and the trust/audit contract a host can rely
on.

## Lifecycle

```
PENDING ──tap──► DECIDED ──host confirms──► EXECUTED ✓
   │              (APPROVED/REJECTED         or FAILED ✗
   │               + who + when stamp,
   │               buttons removed)
   └──timeout──► EXPIRED (default action fires, stamp says so)
```

1. `publish_interactive()` renders a PENDING card with an inline keyboard
   and stores a single-use decision token (Telegram `callback_data` is
   64-byte-capped, so buttons carry only `f"{token}:{action_id}"` — see
   "Token / 64-byte note" below).
2. A tap fires an instant `answerCallbackQuery` toast, consumes the token
   (double-taps and races are no-ops — `PendingStore.consume` is a single
   atomic `UPDATE ... WHERE status = 'pending'`), and checks the tapping
   user against `authorized_users` (unauthorized taps get a refusal toast,
   logged, card unchanged).
3. **First visual ack:** the same template re-renders with the decision
   state layer (APPROVED/REJECTED, decider, timestamp) via
   `editMessageMedia`, keyboard removed. `on_decision` fires.
4. **Second visual ack:** when the host's own execution path knows the real
   outcome, it calls `update_state(..., state="executed"|"failed", ...)` and
   the card re-renders once more, patched with real facts (e.g. fill
   price). The card ends up reflecting reality, not the tap.
5. Timeout: `default_action` fires and the card re-renders EXPIRED — a
   stale card can never be mistaken for a live decision.

## API usage (from the actual signatures)

```python
import factpress as fp

ff = fp.FactPress(telegram_token=BOT_TOKEN, default_chat_id=CHAT_ID)

def gate_callback(result: fp.DecisionResult) -> None:
    audit_logger.info(
        "factpress decision",
        source="factpress_gate",
        token=result.token,
        decision=result.decision,
        user_id=result.user_id,
        decided_at=result.decided_at.isoformat(),
        expired=result.expired,
    )
    if result.decision == "approve":
        place_order(...)

approval = ff.publish_interactive(
    facts=proposal_facts, event_type="trade_proposal",
    actions=[("approve", "Approve"), ("reject", "Reject")],
    authorized_users=[MY_USER_ID],
    timeout_s=60, default_action="reject",
    on_decision=gate_callback,
    chat_id=BOT1_CHAT_ID,
)

# -- callback delivery: pick ONE of the two, see "Integration surface" below --
ff.handle_callback(update)   # from your own dispatcher's update loop
ff.run_poller()              # OR: block forever running the built-in poller

# -- from the execution path, once the real outcome is known --
ff.update_state(approval, state="executed", facts_patch={"fill_price": 2841.5})

# -- periodically (handle_callback path only; run_poller does this for you) --
ff.check_expired()
```

## Integration surface

FactPress is a library, not a daemon, so callback delivery is pluggable:

- **`ff.handle_callback(update)`** — call it from an existing dispatcher
  (e.g. a python-telegram-bot loop the host already runs). The host owns
  the poll/webhook loop and its own periodic `ff.check_expired()` call.
- **`ff.run_poller()`** — an optional built-in, blocking `getUpdates`
  long-poll loop for standalone users with no dispatcher of their own. It
  drives both `handle_callback` (per `callback_query` update) and
  `check_expired` (every `expiry_sweep_every_s`) itself.

```python
# run_poller signature (factpress/__init__.py):
ff.run_poller(
    stop_event=None,            # threading.Event | None -- set it to stop
    poll_timeout_s=25,          # Telegram long-poll timeout
    expiry_sweep_every_s=15.0,  # how often check_expired() runs
)

# or drive the Poller class directly (factpress/interactive/poller.py):
from factpress.interactive.poller import Poller
poller = Poller(manager, publisher_config)  # or a bare token string
thread, stop_event = poller.start_in_thread()
...
stop_event.set()
thread.join()
```

`Poller.run()` tracks its own Telegram `offset` (advancing past every
`update_id` it sees, so nothing is redelivered), restricts
`allowed_updates` to `["callback_query"]`, and is resilient by design: an
exception out of `handle_callback` for one bad update, or out of
`check_expired`, is logged and the loop continues; transport errors and
non-200 `getUpdates` responses get capped exponential backoff (1s, 2s, 4s,
8s) and also do not stop the loop. `stop_event` is checked at the top of
every iteration, so setting it stops the loop promptly *between*
iterations — the long-poll HTTP call itself still blocks for up to
`poll_timeout_s` seconds once it is in flight, which bounds worst-case
shutdown latency to roughly `poll_timeout_s`, not the sweep interval.

## The audit-trail contract

Every decision — tap or expiry — is emitted to the host as a
`DecisionResult`:

```python
@dataclass
class DecisionResult:
    token: str
    decision: str          # the action_id, e.g. "approve" / "reject"
    user_id: int | None    # tapping user's Telegram id; None for expiry
    decided_at: datetime   # UTC
    approval: PendingApproval
    expired: bool = False
```

`on_decision` is the delivery mechanism — wire it into a host audit logger:

```python
def on_decision(result: fp.DecisionResult) -> None:
    audit_logger.info(
        "factpress_gate_decision",
        source="factpress_gate",   # AI-Trading's audit_logger reference convention
        token=result.token,
        decision=result.decision,
        user_id=result.user_id,
        decided_at=result.decided_at.isoformat(),
        expired=result.expired,
    )
```

This is the design's reference consumer: in AI-Trading, the human-gate flow
feeds exactly this into `audit_logger` with `source=factpress_gate`, so
every trade decision — who, what, when, and whether it was a real tap or a
timeout default — lands in the same audit trail as everything else the
system does.

## Restart semantics

`on_decision` callbacks live in an in-memory `{token: callable}` dict —
**they do not survive a process restart.** On startup, call
`ff.resume_after_restart()`: it sweeps any row still `"pending"` past its
deadline (orphaned by the restart) and re-renders each as EXPIRED, exactly
like a live `check_expired()` sweep would have — but it does **not** fire
`on_decision` for them (there is no callback left to fire). A host that
needs guaranteed delivery across restarts should treat the returned token
list as the source of truth, not the callback:

```python
orphaned_tokens = ff.resume_after_restart()
for token in orphaned_tokens:
    audit_logger.info("factpress_gate_decision", source="factpress_gate",
                       token=token, decision="expired-on-restart")
```

The pending-approvals store itself (`PendingStore`, SQLite on disk) is what
survives — it is why `resume_after_restart` can find these rows at all.

## Custom gates: `action_states` (confirm/cancel kill-switch example)

The default action ids are `approve`/`reject`, mapping to
`CardState.APPROVED`/`CardState.REJECTED`. A host with a differently-named
gate — e.g. a kill-switch confirmation — supplies its own `action_states`
at `InteractiveManager` construction; every action id must resolve to
`"approved"` or `"rejected"`:

```python
manager = InteractiveManager(
    store, publisher,
    action_states={"confirm": "approved", "cancel": "rejected"},
)
manager.publish_interactive(
    halt_facts, "kill_switch",
    actions=[("confirm", "Confirm halt"), ("cancel", "Keep running")],
    authorized_users=[OPS_USER_ID],
    timeout_s=30, default_action="cancel",
    chat_id=OPS_CHAT_ID,
)
```

A `confirm` tap re-renders as `CardState.APPROVED` (the "HALTED" stamp
doubles as the visual record of who stopped the system and when — §7's
"Kill-switch confirmation" use case); a `cancel` tap or a timeout re-renders
as `CardState.REJECTED`. Any `action_states` value that isn't `"approved"`
or `"rejected"` raises `ValueError` at construction time — the mapping is
checked before any card is ever published.

## Token / 64-byte note

Telegram caps `callback_data` at 64 bytes. Each button encodes
`f"{token}:{action_id}"`; `PendingStore.create` mints the token via
`secrets.token_urlsafe(32)` — 43 ASCII characters — leaving comfortable
room for a separator and a short `action_id` well under the cap. The bare
token alone can't distinguish which of a card's several buttons was
tapped, which is why the action id rides alongside it; it is validated
against the row's own declared `actions` before anything else runs, and an
unrecognized action id is treated exactly like an unknown token (refusal
toast, no state change).

## Determinism boundary

State visuals — ribbons, stamps, dimming, button layout — are **template
state layers**, enumerated in each template's manifest and rendered
deterministically. The director LLM (if configured) art-directs only the
*proposal content* — which facts to feature, headline/subhead wording —
exactly as in one-way `render`/`publish` mode. It has **zero control** over
which state layer renders, what a button says, or how an acknowledgement
looks: those are template-manifest data and `CardState` enum values, never
LLM output. Golden tests cover every state of every interactive template.
