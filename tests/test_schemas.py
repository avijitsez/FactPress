import pytest
from pydantic import ValidationError

from factpress.schemas import (
    DailyPnlFacts,
    DesignSpec,
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
