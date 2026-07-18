import pytest
from pydantic import ValidationError

from factpress.schemas import (
    DailyPnlFacts,
    DesignSpec,
    DigestTopPicksFacts,
    MilestoneFacts,
    PickItem,
    PulseUpdateFacts,
    ReflectionRecapFacts,
    SessionDigestFacts,
    SpecFactsMismatch,
    Tone,
    validate_spec_for_facts,
)


def _valid_spec_kwargs(**overrides):
    kwargs = dict(
        template_id="daily_pnl",
        template_version="1.0.0",
        variant="default",
        tone=Tone.NEUTRAL,
        palette_id="default",
        hero_metric_key="daily_pnl_pct",
        emphasis_keys=["equity"],
        callout_keys=["win_rate_pct"],
        headline="Steady session",
        subhead="Quiet day across the board",
        caption="Automated summary.",
        emoji="up",
        sparkline=True,
    )
    kwargs.update(overrides)
    return kwargs


def test_design_spec_round_trip():
    spec = DesignSpec(**_valid_spec_kwargs())
    dumped = spec.model_dump()
    reloaded = DesignSpec(**dumped)
    assert reloaded == spec


@pytest.mark.parametrize(
    "field,value",
    [
        ("headline", "Up 5% day"),
        ("subhead", "Up 5% day"),
        ("caption", "Up 5% day"),
        ("emoji", "5%"),
    ],
)
def test_digit_rejection(field, value):
    with pytest.raises(ValidationError, match="digits are forbidden"):
        DesignSpec(**_valid_spec_kwargs(**{field: value}))


def test_headline_length_cap():
    DesignSpec(**_valid_spec_kwargs(headline="x" * 60))
    with pytest.raises(ValidationError):
        DesignSpec(**_valid_spec_kwargs(headline="x" * 61))


def test_extra_field_forbidden():
    with pytest.raises(ValidationError):
        DesignSpec(**_valid_spec_kwargs(unexpected_field="nope"))


def test_emphasis_keys_max_length():
    DesignSpec(**_valid_spec_kwargs(emphasis_keys=["a", "b", "c"]))
    with pytest.raises(ValidationError):
        DesignSpec(**_valid_spec_kwargs(emphasis_keys=["a", "b", "c", "d"]))


def test_callout_keys_max_length():
    DesignSpec(**_valid_spec_kwargs(callout_keys=["a", "b", "c", "d"]))
    with pytest.raises(ValidationError):
        DesignSpec(**_valid_spec_kwargs(callout_keys=["a", "b", "c", "d", "e"]))


def test_template_version_semver():
    with pytest.raises(ValidationError):
        DesignSpec(**_valid_spec_kwargs(template_version="1.0"))
    DesignSpec(**_valid_spec_kwargs(template_version="1.0.0"))


def test_daily_pnl_facts_valid_payload_and_metric_keys():
    facts = DailyPnlFacts(
        daily_pnl_pct=1.5,
        daily_pnl_abs=150.0,
        equity=10500.0,
        win_rate_pct=62.5,
        trades_count=8,
        label="aggressive",
        extra_metric=42,
    )
    keys = facts.metric_keys()
    assert "daily_pnl_pct" in keys
    assert "extra_metric" in keys
    assert "label" not in keys
    assert "event_type" not in keys


def test_daily_pnl_series_max_length():
    DailyPnlFacts(daily_pnl_pct=1.0, series=[0.0] * 500)
    with pytest.raises(ValidationError):
        DailyPnlFacts(daily_pnl_pct=1.0, series=[0.0] * 501)


def _valid_facts(**overrides):
    kwargs = dict(
        daily_pnl_pct=1.5,
        equity=10000.0,
        win_rate_pct=60.0,
        series=[1.0, 2.0, 3.0],
    )
    kwargs.update(overrides)
    return DailyPnlFacts(**kwargs)


def test_validate_spec_for_facts_happy_path():
    spec = DesignSpec(**_valid_spec_kwargs())
    facts = _valid_facts()
    validate_spec_for_facts(spec, facts)


def test_validate_spec_for_facts_hero_key_missing():
    spec = DesignSpec(**_valid_spec_kwargs(hero_metric_key="nonexistent_key"))
    facts = _valid_facts()
    with pytest.raises(SpecFactsMismatch) as exc_info:
        validate_spec_for_facts(spec, facts)
    assert any("nonexistent_key" in v for v in exc_info.value.violations)
    assert "nonexistent_key" in str(exc_info.value)


def test_validate_spec_for_facts_emphasis_key_missing():
    spec = DesignSpec(**_valid_spec_kwargs(emphasis_keys=["nonexistent_key"]))
    facts = _valid_facts()
    with pytest.raises(SpecFactsMismatch) as exc_info:
        validate_spec_for_facts(spec, facts)
    assert any(
        "nonexistent_key" in v and "emphasis_keys" in v
        for v in exc_info.value.violations
    )


def test_validate_spec_for_facts_callout_key_missing():
    spec = DesignSpec(**_valid_spec_kwargs(callout_keys=["nonexistent_key"]))
    facts = _valid_facts()
    with pytest.raises(SpecFactsMismatch) as exc_info:
        validate_spec_for_facts(spec, facts)
    assert any(
        "nonexistent_key" in v and "callout_keys" in v
        for v in exc_info.value.violations
    )


def test_validate_spec_for_facts_duplicate_emphasis_keys():
    spec = DesignSpec(**_valid_spec_kwargs(emphasis_keys=["equity", "equity"]))
    facts = _valid_facts()
    with pytest.raises(SpecFactsMismatch) as exc_info:
        validate_spec_for_facts(spec, facts)
    assert any(
        "emphasis_keys" in v and "duplicate" in v for v in exc_info.value.violations
    )


def test_validate_spec_for_facts_duplicate_callout_keys():
    spec = DesignSpec(
        **_valid_spec_kwargs(callout_keys=["win_rate_pct", "win_rate_pct"])
    )
    facts = _valid_facts()
    with pytest.raises(SpecFactsMismatch) as exc_info:
        validate_spec_for_facts(spec, facts)
    assert any(
        "callout_keys" in v and "duplicate" in v for v in exc_info.value.violations
    )


def test_validate_spec_for_facts_hero_key_repeated_in_emphasis():
    spec = DesignSpec(**_valid_spec_kwargs(emphasis_keys=["daily_pnl_pct"]))
    facts = _valid_facts()
    with pytest.raises(SpecFactsMismatch) as exc_info:
        validate_spec_for_facts(spec, facts)
    assert any(
        "daily_pnl_pct" in v and "emphasis_keys" in v
        for v in exc_info.value.violations
    )


def test_validate_spec_for_facts_celebratory_on_red_day():
    spec = DesignSpec(**_valid_spec_kwargs(tone=Tone.CELEBRATORY))
    facts = _valid_facts(daily_pnl_pct=-2.5)
    with pytest.raises(SpecFactsMismatch) as exc_info:
        validate_spec_for_facts(spec, facts)
    assert any(
        "celebratory" in v and "red day" in v for v in exc_info.value.violations
    )


def test_validate_spec_for_facts_sparkline_requires_series():
    spec = DesignSpec(**_valid_spec_kwargs(sparkline=True))
    facts = DailyPnlFacts(daily_pnl_pct=1.0, equity=100.0, win_rate_pct=50.0)
    with pytest.raises(SpecFactsMismatch) as exc_info:
        validate_spec_for_facts(spec, facts)
    assert any("sparkline requires a series" in v for v in exc_info.value.violations)


def test_validate_spec_for_facts_sparkline_requires_at_least_two_points():
    spec = DesignSpec(**_valid_spec_kwargs(sparkline=True))
    facts = _valid_facts(series=[1.0])
    with pytest.raises(SpecFactsMismatch) as exc_info:
        validate_spec_for_facts(spec, facts)
    assert any("sparkline requires a series" in v for v in exc_info.value.violations)


def test_validate_spec_for_facts_multiple_violations_collected():
    spec = DesignSpec(
        **_valid_spec_kwargs(hero_metric_key="nonexistent_key", sparkline=True)
    )
    facts = DailyPnlFacts(daily_pnl_pct=1.0)
    with pytest.raises(SpecFactsMismatch) as exc_info:
        validate_spec_for_facts(spec, facts)
    assert len(exc_info.value.violations) >= 2
    assert any("nonexistent_key" in v for v in exc_info.value.violations)
    assert any("sparkline requires a series" in v for v in exc_info.value.violations)


def test_validate_spec_for_facts_celebratory_with_positive_hero_passes():
    spec = DesignSpec(**_valid_spec_kwargs(tone=Tone.CELEBRATORY))
    facts = _valid_facts(daily_pnl_pct=5.0)
    validate_spec_for_facts(spec, facts)


@pytest.mark.parametrize("tone", [Tone.NEUTRAL, Tone.CAUTIONARY])
def test_validate_spec_for_facts_non_celebratory_with_negative_hero_passes(tone):
    spec = DesignSpec(**_valid_spec_kwargs(tone=tone))
    facts = _valid_facts(daily_pnl_pct=-3.0)
    validate_spec_for_facts(spec, facts)


def test_validate_spec_for_facts_sparkline_false_with_no_series_passes():
    spec = DesignSpec(**_valid_spec_kwargs(sparkline=False))
    facts = DailyPnlFacts(daily_pnl_pct=1.0, equity=100.0, win_rate_pct=50.0)
    validate_spec_for_facts(spec, facts)


@pytest.mark.parametrize(
    "sneaky",
    [
        "Portfolio up ⑨⑨⑨ percent",  # circled digits (category No)
        "Up Ⅲ points",  # roman numeral III (category Nl)
        "Gained ½ of target",  # vulgar fraction 1/2 (category No)
        "Up ¹² percent",  # superscript digits (category No)
        "Up ٩ points",  # Arabic-Indic digit nine (category Nd)
    ],
)
def test_digit_ban_covers_all_unicode_numerals(sneaky):
    with pytest.raises(ValidationError, match="digits are forbidden"):
        DesignSpec(**_valid_spec_kwargs(headline=sneaky))


# --- F3: five new event-type fact models ---------------------------------


def test_pulse_update_facts_valid_payload():
    facts = PulseUpdateFacts(
        mode_pnl_pct=0.8,
        open_positions_count=3,
        orders_used_count=2,
        orders_cap_count=10,
        series=[1.0, 1.2, 0.9, 1.4],
        label="aggressive",
    )
    assert facts.event_type == "pulse_update"
    assert facts.open_positions_count == 3


def test_pulse_update_facts_rejects_negative_open_positions():
    with pytest.raises(ValidationError):
        PulseUpdateFacts(mode_pnl_pct=0.8, open_positions_count=-1)


def test_pulse_update_facts_series_max_length():
    with pytest.raises(ValidationError):
        PulseUpdateFacts(
            mode_pnl_pct=0.1, open_positions_count=0, series=[0.0] * 501
        )


def test_session_digest_facts_valid_open_payload():
    facts = SessionDigestFacts(
        session="open",
        watchlist_symbols=["AAPL", "MSFT"],
        regime="risk-on",
        plan_notes=["Watch for breakout above resistance."],
        label="premarket",
    )
    assert facts.event_type == "session_digest"
    assert facts.session == "open"


def test_session_digest_facts_valid_close_payload():
    facts = SessionDigestFacts(
        session="close",
        realized_pnl_pct=1.2,
        realized_pnl_abs=340.0,
        hit_count=3,
        miss_count=1,
    )
    assert facts.session == "close"


def test_session_digest_facts_rejects_too_many_watchlist_symbols():
    with pytest.raises(ValidationError):
        SessionDigestFacts(session="open", watchlist_symbols=["A"] * 13)


def test_session_digest_facts_rejects_long_watchlist_symbol():
    with pytest.raises(ValidationError, match="watchlist_symbols"):
        SessionDigestFacts(session="open", watchlist_symbols=["X" * 21])


def test_session_digest_facts_rejects_too_many_plan_notes():
    with pytest.raises(ValidationError):
        SessionDigestFacts(session="open", plan_notes=["note"] * 6)


def test_session_digest_facts_rejects_long_plan_note():
    with pytest.raises(ValidationError, match="plan_notes"):
        SessionDigestFacts(session="open", plan_notes=["x" * 121])


def test_digest_top_picks_facts_valid_payload():
    facts = DigestTopPicksFacts(
        picks=[
            PickItem(symbol="AAPL", score=0.91, direction="long", note="Momentum breakout"),
            PickItem(symbol="TSLA", score=0.74, direction="short"),
        ]
    )
    assert facts.event_type == "digest_top_picks"
    assert len(facts.picks) == 2


def test_digest_top_picks_facts_rejects_too_many_picks():
    with pytest.raises(ValidationError):
        DigestTopPicksFacts(
            picks=[PickItem(symbol=f"S{i}", score=0.5) for i in range(11)]
        )


def test_digest_top_picks_facts_rejects_empty_picks():
    with pytest.raises(ValidationError):
        DigestTopPicksFacts(picks=[])


def test_milestone_facts_valid_payload():
    facts = MilestoneFacts(
        milestone_kind="win_streak",
        streak_count=7,
        record_value=12.5,
        record_unit="%",
        previous_best=10.0,
        label="streak",
    )
    assert facts.event_type == "milestone"
    assert facts.milestone_kind == "win_streak"


def test_milestone_facts_rejects_long_milestone_kind():
    with pytest.raises(ValidationError):
        MilestoneFacts(milestone_kind="x" * 41)


def test_milestone_facts_rejects_long_record_unit():
    with pytest.raises(ValidationError):
        MilestoneFacts(milestone_kind="win_streak", record_unit="x" * 21)


def test_reflection_recap_facts_valid_payload():
    facts = ReflectionRecapFacts(
        week_pnl_pct=2.4,
        trades_count=14,
        win_rate_pct=57.0,
        best_day_pct=3.1,
        worst_day_pct=-1.2,
        reflection_candidates=[
            "Stayed disciplined on stop-losses even during the midweek dip.",
            "Overtraded on Thursday chasing a breakout that never confirmed.",
        ],
        label="weekly",
    )
    assert facts.event_type == "reflection_recap"
    assert len(facts.reflection_candidates) == 2


def test_reflection_recap_facts_rejects_too_long_candidate():
    with pytest.raises(ValidationError):
        ReflectionRecapFacts(week_pnl_pct=1.0, reflection_candidates=["x" * 501])


def test_reflection_recap_facts_rejects_too_short_candidate():
    with pytest.raises(ValidationError):
        ReflectionRecapFacts(week_pnl_pct=1.0, reflection_candidates=["short"])


def test_reflection_recap_facts_rejects_empty_candidates():
    with pytest.raises(ValidationError):
        ReflectionRecapFacts(week_pnl_pct=1.0, reflection_candidates=[])


def test_reflection_recap_facts_rejects_too_many_candidates():
    with pytest.raises(ValidationError):
        ReflectionRecapFacts(
            week_pnl_pct=1.0,
            reflection_candidates=["x" * 30] * 6,
        )


def test_design_spec_reflection_index_round_trips():
    # reflection_index is an int field, not copy -- it is exempt from the
    # digit ban and round-trips like any other structural reference field.
    spec = DesignSpec(**_valid_spec_kwargs(reflection_index=2))
    dumped = spec.model_dump()
    reloaded = DesignSpec(**dumped)
    assert reloaded == spec
    assert reloaded.reflection_index == 2
