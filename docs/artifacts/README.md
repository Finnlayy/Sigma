# Downloaded artifacts (Sigma)

| Artifact | Canonical in-repo path | Notes |
|----------|------------------------|-------|
| `autonomy-level-4.yaml` | `config/autonomy-level-4.yaml` | Byte-identical to Finn Downloads copy (2026-09-12). Loaded by `app/core/l4_config.py`; mirrored in `app/core/blueprint.py` / `tests/test_blueprint_spec.py`. |
| TradingView Lightweight Charts Integration Guide.docx | `docs/guides/TradingView-Lightweight-Charts-Integration.md` | Extracted markdown; critical path implemented in `TvLightweightChart.tsx` + `/ws/market-feed/{symbol}`. |

Do not commit binary `.docx` unless product requires it — markdown extract is the working copy.
