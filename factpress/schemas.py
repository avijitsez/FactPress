"""Pydantic schemas for FactPress facts and design specs.

Three-stage separation, encoded as types:

- ``FactPayload`` (and subclasses like ``DailyPnlFacts``) carry the numeric
  and structural facts a host system already knows to be true. They allow
  arbitrary extra metric keys so host systems can attach whatever numeric
  fields they need without a schema change.

- ``DesignSpec`` is what the creative-director LLM produces. It has **no
  free numeric fields** — the LLM never writes a number into the design. It
  instead references facts *by key* (``hero_metric_key``, ``emphasis_keys``,
  ``callout_keys``), and the renderer resolves those keys against the facts
  payload, formatting every numeral itself.

  ``template_version`` is the one field that looks numeric-ish: it is
  validated as a semver *string* identifying which template contract the
  spec was authored against. Those digits are a version identifier, not
  rendered copy, and are never shown to an end user.

- Hard digit ban: any LLM-authored copy field (``headline``, ``subhead``,
  ``caption``, ``emoji``) rejects any string containing a digit (``\\d``) at
  validation time. Numerals only ever enter the rendered image through the
  renderer's own formatting of facts, never through LLM prose.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_DIGIT_RE = re.compile(r"\d")

_DIGIT_BAN_MESSAGE = (
    "digits are forbidden in LLM-authored copy; "
    "numerals are rendered from facts by key"
)


def _reject_digits(value: str | None) -> str | None:
    """Shared validator body: raise if `value` contains any digit character."""
    if value is not None and _DIGIT_RE.search(value):
        raise ValueError(_DIGIT_BAN_MESSAGE)
    return value


class Tone(StrEnum):
    CELEBRATORY = "celebratory"
    NEUTRAL = "neutral"
    CAUTIONARY = "cautionary"


class FactPayload(BaseModel):
    """Base class for all fact payloads.

    Host systems attach arbitrary metric keys beyond the declared fields, so
    extra fields are allowed rather than forbidden.
    """

    model_config = ConfigDict(extra="allow")

    event_type: str
    as_of: datetime | None = None

    def metric_keys(self) -> set[str]:
        """Return the names of all numeric (int/float) fields, including
        any extra fields attached beyond the declared schema.

        Booleans are excluded even though ``bool`` is a subclass of ``int``
        in Python, since a boolean flag is not a metric.
        """
        keys: set[str] = set()
        for name, value in self:
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                keys.add(name)
        return keys


class DailyPnlFacts(FactPayload):
    """Facts for a daily profit-and-loss notification."""

    event_type: Literal["daily_pnl"] = "daily_pnl"
    daily_pnl_pct: float
    daily_pnl_abs: float | None = None
    currency: str = "USD"
    equity: float | None = None
    series: list[float] | None = Field(default=None, max_length=500)
    win_rate_pct: float | None = None
    trades_count: int | None = None
    label: str | None = Field(default=None, max_length=40)


class DesignSpec(BaseModel):
    """The creative director's output. Zero free numeric fields.

    Every metric reference is a *key* into the facts payload
    (``hero_metric_key``, ``emphasis_keys``, ``callout_keys``); the renderer
    resolves those keys and formats the numerals. Extra fields are forbidden
    so the LLM cannot smuggle in anything outside this contract.
    """

    model_config = ConfigDict(extra="forbid")

    template_id: str
    template_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    variant: str = "default"
    tone: Tone
    palette_id: str
    hero_metric_key: str
    emphasis_keys: list[str] = Field(default_factory=list, max_length=3)
    callout_keys: list[str] = Field(default_factory=list, max_length=4)
    headline: str = Field(min_length=1, max_length=60)
    subhead: str | None = Field(default=None, max_length=90)
    caption: str | None = Field(default=None, max_length=200)
    emoji: str | None = Field(default=None, max_length=4)
    sparkline: bool = True

    @field_validator("headline", "subhead", "caption", "emoji")
    @classmethod
    def _no_digits(cls, v: str | None) -> str | None:
        return _reject_digits(v)
