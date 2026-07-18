import pytest
from pydantic import ValidationError

from factpress.schemas import DailyPnlFacts, DesignSpec, Tone


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
