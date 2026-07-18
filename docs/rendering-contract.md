# Render context contract

The engine (factpress/renderer/engine_svg.py) builds this context; templates
consume it. This is the binding interface for template authors.

Templates contain NO formatting logic — every numeral arrives pre-formatted in `view`.

```python
context = {
  "W": int, "H": int,                    # canvas size for the chosen export
  "size": "feed" | "telegram",           # 1080x1350 | 1280x720
  "spec": {...},                          # DesignSpec.model_dump()
  "brand": {
    "fonts": {"sans": "Inter", "mono": "JetBrains Mono"},
    "logo_text": str, "watermark": bool,
    "palette": {  # the single palette selected via spec.palette_id
      "bg","surface","fg","muted","accent","positive","negative",
      "grad_from","grad_to","chip_bg"
    },
  },
  "view": {
    "headline": str, "subhead": str|None,
    "emoji": None,   # DECISION (LEAD, F0): always None in the image — vendored fonts
                     # carry no emoji glyphs. spec.emoji ships in the Telegram
                     # CAPTION only (F2 publisher prepends it). Key kept for shape
                     # compatibility with templates that {% if %}-guard it.
    "hero": {"label": str, "value": str,          # value fully formatted: sign, %, currency
              "direction": "up"|"down"|"flat",
              "color_role": "positive"|"negative"|"neutral"},
    "delta_chips": [ {"label": str, "value": str, "direction": ...} ],  # from emphasis_keys
    "callouts":    [ {"label": str, "value": str} ],                    # from callout_keys
    "sparkline": {"path": str, "area_path": str, "w": int, "h": int} | None,
    "as_of": str|None,                    # e.g. "18 Jul 2026, 15:04 UTC"
    "footer": str,                        # compliance footer from brandkit
  },
}
```

Rules:
- format.py owns all numeral/label formatting. Key heuristics: `*_pct` → signed
  percent 2dp; `*_abs`/`equity`/`price*` → currency; `*_count`/`trades_count` → int
  with thousands separators. `humanize_key()` maps known keys to display labels
  (daily_pnl_pct → "Daily P&L", win_rate_pct → "Win rate", ...) with a generic
  title-case fallback.
- NO `locale` stdlib module anywhere (OS-dependent = nondeterministic). English
  month abbreviations hardcoded. All float coords in SVG paths fixed to 2dp.
- sparkline.py: `build_paths(series, w, h, pad=...) -> (path, area_path)`,
  deterministic string output, handles len 0/1 (returns None upstream), flat
  series (horizontal midline), rejects NaN/inf with ValueError.
- Templates are parametrized by W/H and must lay out sensibly at BOTH
  1080x1350 and 1280x720 (Jinja arithmetic from W/H, or explicit per-size
  blocks. No hardcoded canvas size).
- Manifest schema (manifest.yaml per template):
  id, version (semver), name, variants (list), sizes {feed:[w,h], telegram:[w,h]},
  slots {headline:{max}, subhead:{max}, emoji:{max}}, palettes_allowed (list),
  sparkline: required|optional|none, states: [static].
