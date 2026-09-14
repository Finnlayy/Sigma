"""
=========================================================
Datei:      app/dashboard/tv_lightweight_export.py
Zweck:      MP-16 Forschungs-Export: Backtest-Lauf (oder TV-CSV
            + Indikatoren) als eigenständiges Offline-HTML mit
            drei synchronisierten Lightweight-Charts-Panes
            (CDN standalone, kein Bundler): Candles+Marker,
            cos phi mit Schwellen-Linien, Equity. Reines
            Offline-Dashboard — keine Live-Verbindung, keine
            Order-Funktion, kein Polling.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Dashboard)
=========================================================
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sigma.backtest.power_factor_backtest import PowerFactorResult

HOUR_SECONDS = 3600

_CDN = "https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Sigma MP-16 Research — cos-phi Backtest</title>
<style>
  body {{ background:#111; color:#d0d0d0; font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin:0; }}
  h1 {{ font-size:16px; padding:12px 16px; margin:0; border-bottom:1px solid #2a2a2a; }}
  .pane {{ height:300px; margin:8px 12px; }}
  #status {{ padding:4px 16px; font-size:12px; color:#8a8a8a; }}
</style>
</head>
<body>
<h1>Sigma MP-16 — cos-phi Backtest (offline)</h1>
<div id="status">Research-only Dashboard. Keine Live-Daten, keine Orders.</div>
<div id="pane-candles" class="pane"></div>
<div id="pane-cos" class="pane"></div>
<div id="pane-equity" class="pane"></div>
<script id="sigma-data" type="application/json">{payload_json}</script>
<script src="{cdn}"></script>
<script>
(function () {{
  const data = JSON.parse(document.getElementById('sigma-data').textContent);
  const charts = [];
  function mkChart(el) {{
    const c = LightweightCharts.createChart(el, {{
      layout: {{ background: {{ type: 'solid', color: '#111' }}, textColor: '#d0d0d0' }},
      grid: {{ vertLines: {{ color: '#222' }}, horzLines: {{ color: '#222' }} }},
      timeScale: {{ borderColor: '#333' }},
      rightPriceScale: {{ borderColor: '#333' }},
      crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
      autoSize: true,
    }});
    charts.push(c);
    return c;
  }}
  function sync(c1, c2, c3) {{
    c1.timeScale().subscribeVisibleLogicalRangeChange(function (range) {{
      if (range) {{ c2.timeScale().setVisibleLogicalRange(range); c3.timeScale().setVisibleLogicalRange(range); }}
    }});
  }}
  // Pane 1: Candlesticks + Marker
  const c1 = mkChart(document.getElementById('pane-candles'));
  const candles = c1.addCandlestickSeries({{ upColor: '#26a69a', downColor: '#ef5350',
      borderUpColor: '#26a69a', borderDownColor: '#ef5350', wickUpColor: '#26a69a', wickDownColor: '#ef5350' }});
  candles.setData(data.candles);
  const markers = data.markers.map(function (m) {{
    return {{ time: m.time, position: m.position, color: m.color, shape: m.shape, text: m.text }};
  }});
  candles.setMarkers(markers);
  // Pane 2: cos phi + Schwellen-Linien
  const c2 = mkChart(document.getElementById('pane-cos'));
  const cos = c2.addLineSeries({{ color: '#7e57c2', lineWidth: 2 }});
  cos.setData(data.cos_phi);
  [[data.thresholds.long, '#26a69a'], [data.thresholds.short, '#ef5350'],
   [data.thresholds.exit, '#888888'], [-data.thresholds.exit, '#888888'], [0, '#555555']]
    .forEach(function (t) {{
      cos.createPriceLine({{ price: t[0], color: t[1], lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed,
          axisLabelVisible: true, title: '' }});
    }});
  // Pane 3: Equity + Benchmark
  const c3 = mkChart(document.getElementById('pane-equity'));
  const eq = c3.addLineSeries({{ color: '#ffb74d', lineWidth: 2 }});
  eq.setData(data.equity);
  if (data.benchmark && data.benchmark.length) {{
    const bm = c3.addLineSeries({{ color: '#546e7a', lineWidth: 1 }});
    bm.setData(data.benchmark);
  }}
  sync(c1, c2, c3);
  window.addEventListener('resize', function () {{
    charts.forEach(function (c) {{ c.applyOptions({{ width: c.container().clientWidth, height: 300 }}); }});
  }});
}})();
</script>
</body>
</html>
"""


def _ts(c: Mapping[str, Any]) -> float:
    return float(c.get("ts", c.get("time", 0)) or 0)


def _o(c: Mapping[str, Any]) -> float:
    return float(c.get("o", c.get("open", 0.0)) or 0.0)


def _h(c: Mapping[str, Any]) -> float:
    return float(c.get("h", c.get("high", 0.0)) or 0.0)


def _l(c: Mapping[str, Any]) -> float:
    return float(c.get("l", c.get("low", 0.0)) or 0.0)


def _c(c: Mapping[str, Any]) -> float:
    return float(c.get("c", c.get("close", 0.0)) or 0.0)


def build_payload(
    candles: Sequence[Mapping[str, Any]],
    result: PowerFactorResult,
    *,
    include_benchmark: bool = True,
) -> Dict[str, Any]:
    """JSON-Payload: Kerzen (time UNIX-Sekunden, streng aufsteigend,
    lückenlos für 1h-Bars), cos-phi-Serie, Equity, Marker nur an
    Positionswechseln. Deterministisch."""
    bars = list(candles or [])
    if len(bars) != len(result.positions):
        raise ValueError("candles und Result passen nicht zusammen")
    times = [_ts(b) for b in bars]
    for i in range(1, len(times)):
        if times[i] <= times[i - 1]:
            raise ValueError("Zeiten müssen streng aufsteigend sein")
        if abs(times[i] - times[i - 1] - HOUR_SECONDS) > 1e-6:
            raise ValueError("1h-Bars müssen lückenlos sein (diff == 3600s)")

    candles_out: List[Dict[str, Any]] = []
    for i, b in enumerate(bars):
        candles_out.append({
            "time": int(times[i]),
            "open": round(_o(b), 8),
            "high": round(_h(b), 8),
            "low": round(_l(b), 8),
            "close": round(_c(b), 8),
        })

    cos_out: List[Dict[str, Any]] = []
    for i, v in enumerate(result.cos_phi):
        cos_out.append({"time": int(times[i]), "value": round(float(v), 8)})

    equity_out: List[Dict[str, Any]] = []
    for i, v in enumerate(result.equity):
        equity_out.append({"time": int(times[i]), "value": round(float(v), 8)})

    # Marker nur bei Positionswechseln
    markers: List[Dict[str, Any]] = []
    prev = 0
    for i, p in enumerate(result.positions):
        if p != prev:
            if p > 0:
                markers.append({"time": int(times[i]), "position": "belowBar",
                                "color": "#26a69a", "shape": "arrowUp", "text": "LONG"})
            elif p < 0:
                markers.append({"time": int(times[i]), "position": "aboveBar",
                                "color": "#ef5350", "shape": "arrowDown", "text": "SHORT"})
            else:
                markers.append({"time": int(times[i]), "position": "aboveBar",
                                "color": "#9e9e9e", "shape": "circle", "text": "FLAT"})
            prev = p

    payload: Dict[str, Any] = {
        "candles": candles_out,
        "cos_phi": cos_out,
        "equity": equity_out,
        "markers": markers,
        "thresholds": {
            "long": result.params.long_threshold,
            "short": result.params.short_threshold,
            "exit": result.params.exit_threshold,
        },
        "metrics": {
            "total_return": result.total_return,
            "max_drawdown": result.max_drawdown,
            "sharpe": result.sharpe,
            "win_rate": result.win_rate,
            "profit_factor": result.profit_factor,
            "trade_count": result.trade_count,
        },
    }
    if include_benchmark:
        closes = [_c(b) for b in bars]
        if closes and closes[0] > 0:
            payload["benchmark"] = [
                {"time": int(times[i]), "value": round(c / closes[0], 8)}
                for i, c in enumerate(closes)
            ]
    return payload


def payload_to_json(payload: Mapping[str, Any]) -> str:
    """Deterministische JSON-Serialisierung (sortierte Keys, fester
    Abstand). Kein Zeitstempel, kein Zufall."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def render_html(payload: Mapping[str, Any], output_path: Optional[str] = None) -> str:
    """Standalone-HTML mit drei CDN-Panes (dark theme). Schreibt nach
    output_path, wenn gesetzt; gibt immer den HTML-String zurück."""
    json_blob = payload_to_json(payload).replace("</", "<\\/")
    html = _HTML_TEMPLATE.format(payload_json=json_blob, cdn=_CDN)
    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w") as f:
            f.write(html)
    return html


def export_backtest_html(
    candles: Sequence[Mapping[str, Any]],
    result: PowerFactorResult,
    output_path: str,
    *,
    include_benchmark: bool = True,
) -> str:
    """Bequemlichkeit: Backtest-Lauf direkt als HTML exportieren."""
    payload = build_payload(candles, result, include_benchmark=include_benchmark)
    return render_html(payload, output_path=output_path)
