"""The creative director (F1.2): an OpenAI-compatible LLM client quarantined
behind strict validation, one retry, and a deterministic fallback.

Per FACTPRESS_DESIGN.md §1-2, the director is the *only* nondeterministic
stage in the pipeline, and it never gets to touch a numeral: the LLM
produces a :class:`~factpress.schemas.DesignSpec`, which references facts
*by key* and has no free numeric fields (digit ban on all copy fields,
``extra="forbid"``). Every reply is validated three ways before it is
trusted -- schema (``DesignSpec.model_validate``), facts cross-reference
(``validate_spec_for_facts``), and catalog membership (template id/version,
variant, palette) -- and a bad reply gets exactly one retry with the
rejection reason appended. If the retry also fails, the transport raises,
or the reply isn't JSON at all, :meth:`Director.design` NEVER raises to the
caller: it logs a warning and returns :func:`fallback_spec`, the same
deterministic, zero-LLM spec the CLI's render path uses when there is no
director at all.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from factpress.schemas import (
    DesignSpec,
    FactPayload,
    SpecFactsMismatch,
    Tone,
    validate_spec_for_facts,
)

logger = logging.getLogger("factpress.director")

_EMPHASIS_CANDIDATES = ("win_rate_pct", "trades_count")
_CALLOUT_CANDIDATES = ("equity",)

_SYSTEM_PROMPT_TEMPLATE = """You are the creative director for FactPress, an automated \
infographic notification system. You choose how a fact payload is *presented* -- you never \
choose, write, or alter what the numbers are.

Return ONLY a single JSON object matching this schema. No markdown, no commentary, no code \
fences, no keys beyond these:
  template_id (string), template_version (semver string), variant (string), tone \
("celebratory" | "neutral" | "cautionary"), palette_id (string), hero_metric_key (string), \
emphasis_keys (array of strings, at most 3), callout_keys (array of strings, at most 4), \
headline (string), subhead (string or null), caption (string or null), emoji (string or \
null), sparkline (boolean).

Hard rules -- breaking any of these gets your reply rejected:
- Metrics are referenced BY KEY only. Never write a numeral or digit anywhere in headline, \
subhead, caption, or emoji -- any digit in those fields causes automatic rejection. The \
renderer resolves every key against the facts payload and formats the numeral itself.
- hero_metric_key, and every entry in emphasis_keys and callout_keys, must be one of the \
facts' numeric metric keys (see the facts payload in the next message). No duplicate \
entries, and hero_metric_key must not also appear in emphasis_keys.
- Celebratory tone is forbidden whenever the hero metric's value is negative -- a red day \
cannot be celebratory.
- sparkline may be true only if the facts' series has at least 2 points; otherwise it must \
be false.
- emoji decorates the Telegram CAPTION only. It is never drawn into the rendered image, so \
pick it for the caption's tone, not for on-image layout.
- template_id must equal the catalog entry's id below, template_version must equal its \
version, variant must be one of its variants, and palette_id must be one of its \
palettes_allowed.
- Respect each slot's length cap (see the catalog entry's "slots" below) for headline, \
subhead, caption, and emoji.

Template catalog entry (id, version, variants, slot caps, palettes_allowed):
{catalog_json}

Brand kit palettes available (palette id -> background/accent hex, choose deliberately):
{palette_json}
"""


@dataclass
class DirectorConfig:
    """OpenAI-compatible chat-completions endpoint configuration.

    ``base_url`` is the API root (e.g. ``http://localhost:8100/v1``);
    ``/chat/completions`` is appended when calling out. ``api_key`` is
    optional -- when unset, no ``Authorization`` header is sent.
    """

    base_url: str
    model: str
    api_key: str | None = None
    timeout_s: float = 20.0
    temperature: float = 0.4
    # Reasoning models (e.g. Nemotron) spend thousands of tokens thinking
    # before emitting the spec JSON; a small cap truncates mid-reasoning and
    # yields an unparseable reply. 4096 leaves comfortable headroom.
    max_tokens: int = 4096


class _AttemptFailure(Exception):
    """Internal: one LLM round-trip failed validation (or the transport
    itself failed). Carries the raw reply content (if any was received) so
    the caller can echo it back as the "assistant" turn in a retry."""

    def __init__(self, message: str, raw_content: str | None) -> None:
        super().__init__(message)
        self.raw_content = raw_content


def _strip_code_fence(content: str) -> str:
    """Strip a leading/trailing ```-fenced block (``` or ```json), if present."""
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    lines = lines[1:]  # drop the opening ``` or ```json line
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _facts_message(facts: FactPayload) -> str:
    facts_json = json.dumps(facts.model_dump(mode="json"), indent=2)
    return (
        "Facts payload (reference numeric fields by key; never restate their values):\n"
        f"{facts_json}"
    )


def _build_system_prompt(catalog_entry: dict[str, Any], brandkit: dict[str, Any]) -> str:
    catalog_json = json.dumps(catalog_entry, indent=2)
    palettes = brandkit.get("palettes", {}) if isinstance(brandkit, dict) else {}
    palette_view = {
        palette_id: {"bg": data.get("bg"), "accent": data.get("accent")}
        for palette_id, data in palettes.items()
    }
    palette_json = json.dumps(palette_view, indent=2)
    return _SYSTEM_PROMPT_TEMPLATE.format(catalog_json=catalog_json, palette_json=palette_json)


def _catalog_violations(spec: DesignSpec, catalog_entry: dict[str, Any]) -> list[str]:
    """Check a validated spec against catalog constraints (template id/version,
    variant membership, palette membership). Returns a list of violation
    strings (empty if the spec is consistent with the catalog entry)."""
    violations: list[str] = []
    entry_id = catalog_entry.get("id")
    entry_version = catalog_entry.get("version")
    variants = catalog_entry.get("variants") or []
    palettes_allowed = catalog_entry.get("palettes_allowed") or []

    if spec.template_id != entry_id:
        violations.append(
            f"template_id {spec.template_id!r} does not match catalog id {entry_id!r}"
        )
    if entry_version is not None and spec.template_version != str(entry_version):
        violations.append(
            f"template_version {spec.template_version!r} does not match "
            f"catalog version {entry_version!r}"
        )
    if spec.variant not in variants:
        violations.append(f"variant {spec.variant!r} is not in catalog variants {variants!r}")
    if spec.palette_id not in palettes_allowed:
        violations.append(
            f"palette_id {spec.palette_id!r} is not in catalog palettes_allowed "
            f"{palettes_allowed!r}"
        )
    return violations


class Director:
    """OpenAI-compatible creative-director client.

    ``transport`` lets tests inject an ``httpx.MockTransport`` so
    :meth:`design` never touches the network; production code leaves it
    ``None`` and gets a real ``httpx.Client``.
    """

    def __init__(
        self, config: DirectorConfig, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.config = config
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _endpoint(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/chat/completions"

    def design(
        self,
        facts: FactPayload,
        *,
        catalog_entry: dict[str, Any],
        brandkit: dict[str, Any],
    ) -> DesignSpec:
        """Direct a :class:`DesignSpec` for ``facts``.

        NEVER raises: on any failure (schema, facts cross-reference, catalog
        mismatch, non-JSON reply, or transport error) after one retry, this
        returns :func:`fallback_spec` instead.
        """
        messages: list[dict[str, str]] = [
            {"role": "system", "content": _build_system_prompt(catalog_entry, brandkit)},
            {"role": "user", "content": _facts_message(facts)},
        ]

        client_kwargs: dict[str, Any] = {}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        with httpx.Client(**client_kwargs) as client:
            try:
                return self._attempt(client, messages, facts, catalog_entry)
            except _AttemptFailure as first_failure:
                retry_messages = [
                    *messages,
                    {"role": "assistant", "content": first_failure.raw_content or ""},
                    {
                        "role": "user",
                        "content": (
                            f"Your previous DesignSpec was rejected: {first_failure}. "
                            "Return ONLY corrected JSON."
                        ),
                    },
                ]
                try:
                    return self._attempt(client, retry_messages, facts, catalog_entry)
                except _AttemptFailure as second_failure:
                    logger.warning(
                        "director: LLM spec rejected on retry, falling back to deterministic "
                        "spec: %s",
                        second_failure,
                    )
                    return fallback_spec(facts, manifest=catalog_entry, brandkit=brandkit)

    def _attempt(
        self,
        client: httpx.Client,
        messages: list[dict[str, str]],
        facts: FactPayload,
        catalog_entry: dict[str, Any],
    ) -> DesignSpec:
        try:
            response = client.post(
                self._endpoint(),
                json={
                    "model": self.config.model,
                    "messages": messages,
                    "temperature": self.config.temperature,
                    "max_tokens": self.config.max_tokens,
                },
                headers=self._headers(),
                timeout=self.config.timeout_s,
            )
            response.raise_for_status()
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except httpx.HTTPError as exc:
            raise _AttemptFailure(f"transport error calling LLM: {exc}", None) from exc
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise _AttemptFailure(f"malformed LLM response payload: {exc}", None) from exc

        stripped = _strip_code_fence(content)
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise _AttemptFailure(f"LLM reply was not valid JSON: {exc}", content) from exc

        try:
            spec = DesignSpec.model_validate(data)
        except ValidationError as exc:
            raise _AttemptFailure(f"DesignSpec schema validation failed: {exc}", content) from exc

        try:
            validate_spec_for_facts(spec, facts)
        except SpecFactsMismatch as exc:
            raise _AttemptFailure(
                f"spec/facts mismatch: {'; '.join(exc.violations)}", content
            ) from exc

        catalog_violations = _catalog_violations(spec, catalog_entry)
        if catalog_violations:
            raise _AttemptFailure(
                f"catalog constraint violation: {'; '.join(catalog_violations)}", content
            )

        return spec


def fallback_spec(
    facts: FactPayload, manifest: dict[str, Any], brandkit: dict[str, Any]
) -> DesignSpec:
    """Build the deterministic, zero-LLM fallback `DesignSpec` for `facts`.

    This is the director's guaranteed-safe path (moved from the F0.8 CLI's
    ``default_spec``, behavior-identical): tone is always `Tone.NEUTRAL`, and
    hero-metric/emphasis/callout selection is the simplest heuristic that
    satisfies the schema, not a creative choice. Called both as the CLI's
    zero-LLM render path and as `Director.design`'s worst case.

    `manifest` accepts either an engine manifest dict or a catalog entry
    (`factpress.catalog.catalog_entry` output) -- both shapes carry the
    required keys: `id` (str), `version` (str), `palettes_allowed`
    (non-empty list of str). `brandkit` is accepted (and unused) to keep the
    signature stable for brand-aware defaults later.
    """
    metric_keys = facts.metric_keys()
    if not metric_keys:
        raise ValueError(
            "facts contain no numeric metric fields; cannot choose a hero metric"
        )

    if "daily_pnl_pct" in metric_keys:
        hero_metric_key = "daily_pnl_pct"
    else:
        hero_metric_key = sorted(metric_keys)[0]

    emphasis_keys = [key for key in _EMPHASIS_CANDIDATES if key in metric_keys][:2]
    callout_keys = [key for key in _CALLOUT_CANDIDATES if key in metric_keys]

    # The fallback must be valid by construction against
    # validate_spec_for_facts: only promise a sparkline the renderer can draw.
    series = getattr(facts, "series", None)
    sparkline = isinstance(series, list) and len(series) >= 2

    return DesignSpec(
        template_id=manifest["id"],
        template_version=str(manifest["version"]),
        variant="default",
        tone=Tone.NEUTRAL,
        palette_id=manifest["palettes_allowed"][0],
        hero_metric_key=hero_metric_key,
        emphasis_keys=emphasis_keys,
        callout_keys=callout_keys,
        headline="Daily P&L update",
        subhead=None,
        caption=None,
        sparkline=sparkline,
    )
