/**
 * TvLightweightChart — §8.2a: Primärchart ist Lightweight Charts (OSS),
 * TV-Widget-Embeds bleiben optionale Ergänzung.
 *
 * Implements the TradingView Lightweight Charts Integration Guide critical path:
 * magnet crosshair, setData + update(), series markers, ENTRY/SL/TP price lines,
 * resize observer, clear-on-seriesKey change. Visualization only — no client-side
 * execution math.
 */
import { useEffect, useRef, memo } from 'react';
import {
  createChart,
  ColorType,
  CandlestickSeries,
  LineSeries,
  CrosshairMode,
  LineStyle,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type ISeriesMarkersPluginApi,
  type Time,
} from 'lightweight-charts';
import type { Candle } from '../lib/sigmaApi';

export interface ChartMarker {
  time: number;
  position: 'aboveBar' | 'belowBar' | 'inBar';
  color: string;
  shape: 'arrowUp' | 'arrowDown' | 'circle' | 'square';
  text: string;
}

export interface ChartPriceLine {
  id: string;
  price: number;
  color: string;
  title: string;
  /** 0 Solid, 1 Dotted, 2 Dashed (LWC LineStyle) */
  lineStyle?: 0 | 1 | 2 | 3 | 4;
  lineWidth?: 1 | 2 | 3 | 4;
}

interface Props {
  candles: Candle[];
  equityCurve?: Array<{ ts: number; value: number }>;
  markers?: ChartMarker[];
  priceLines?: ChartPriceLine[];
  /** Change when symbol/interval switches so lines/markers reset cleanly. */
  seriesKey?: string;
  height?: number;
  /** Guide dark terminal theme; default keeps Sigma zinc terminal look. */
  theme?: 'sigma' | 'terminal';
}

function toCandlePoint(c: Candle) {
  return { time: c.ts as Time, open: c.o, high: c.h, low: c.l, close: c.c };
}

function sortByTime<T extends { time: number }>(rows: T[]): T[] {
  return [...rows].sort((a, b) => a.time - b.time);
}

export default memo(function TvLightweightChart({
  candles,
  equityCurve,
  markers,
  priceLines,
  seriesKey = 'default',
  height = 260,
  theme = 'sigma',
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const equitySeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const markersApiRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const priceLineRefs = useRef<Map<string, IPriceLine>>(new Map());
  const lastBarRef = useRef<{ time: number; o: number; h: number; l: number; c: number } | null>(null);
  const lastSeriesKeyRef = useRef(seriesKey);

  useEffect(() => {
    if (!ref.current) return;
    const terminal = theme === 'terminal';
    const chart = createChart(ref.current, {
      height,
      layout: {
        background: {
          type: ColorType.Solid,
          color: terminal ? '#131722' : 'transparent',
        },
        textColor: terminal ? '#d1d4dc' : '#a1a1aa',
        fontSize: 10,
      },
      grid: {
        vertLines: { color: terminal ? 'rgba(42, 46, 57, 0.5)' : 'rgba(63,63,70,0.25)' },
        horzLines: { color: terminal ? 'rgba(42, 46, 57, 0.5)' : 'rgba(63,63,70,0.25)' },
      },
      rightPriceScale: {
        borderColor: terminal ? 'rgba(197, 203, 206, 0.3)' : 'rgba(63,63,70,0.5)',
      },
      timeScale: {
        borderColor: terminal ? 'rgba(197, 203, 206, 0.3)' : 'rgba(63,63,70,0.5)',
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: { mode: CrosshairMode.Magnet },
    });
    chartRef.current = chart;

    candleSeriesRef.current = chart.addSeries(CandlestickSeries, {
      upColor: terminal ? '#26a69a' : '#10b981',
      downColor: terminal ? '#ef5350' : '#ef4444',
      wickUpColor: terminal ? '#26a69a' : '#10b981',
      wickDownColor: terminal ? '#ef5350' : '#ef4444',
      borderVisible: false,
    });

    equitySeriesRef.current = chart.addSeries(LineSeries, {
      color: '#38bdf8',
      lineWidth: 2,
    });

    markersApiRef.current = createSeriesMarkers(candleSeriesRef.current, []);

    const observer = new ResizeObserver(() => {
      chart.applyOptions({ width: ref.current?.clientWidth ?? 300 });
    });
    observer.observe(ref.current);

    return () => {
      observer.disconnect();
      priceLineRefs.current.clear();
      markersApiRef.current = null;
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      equitySeriesRef.current = null;
      lastBarRef.current = null;
    };
  }, [theme]); // remount chart when theme flips

  useEffect(() => {
    if (chartRef.current) {
      chartRef.current.applyOptions({ height });
    }

    const series = candleSeriesRef.current;
    if (!series) return;

    const seriesKeyChanged = lastSeriesKeyRef.current !== seriesKey;
    if (seriesKeyChanged) {
      lastSeriesKeyRef.current = seriesKey;
      lastBarRef.current = null;
      // Clear price lines on symbol/interval switch (guide §7 memory mgmt)
      for (const line of priceLineRefs.current.values()) {
        try {
          series.removePriceLine(line);
        } catch {
          /* series may already be gone */
        }
      }
      priceLineRefs.current.clear();
      markersApiRef.current?.setMarkers([]);
    }

    if (!candles.length) {
      series.setData([]);
      lastBarRef.current = null;
    } else {
      const sorted = [...candles].sort((a, b) => a.ts - b.ts);
      const last = sorted[sorted.length - 1];
      const prev = lastBarRef.current;
      // Guide §4: update() when the open bar ticks or a new bar appends;
      // full setData on history reload / series switch.
      const sameOpenBar =
        !seriesKeyChanged && prev !== null && last.ts === prev.time && sorted.length >= 1;
      const appendedBar =
        !seriesKeyChanged &&
        prev !== null &&
        last.ts > prev.time &&
        sorted.length >= 2 &&
        sorted[sorted.length - 2]?.ts === prev.time;
      if (sameOpenBar || appendedBar) {
        series.update(toCandlePoint(last));
      } else {
        series.setData(sorted.map(toCandlePoint));
      }
      lastBarRef.current = { time: last.ts, o: last.o, h: last.h, l: last.l, c: last.c };
    }

    if (equitySeriesRef.current) {
      if (equityCurve?.length) {
        equitySeriesRef.current.setData(
          equityCurve.map((p) => ({ time: p.ts as Time, value: p.value })),
        );
      } else {
        equitySeriesRef.current.setData([]);
      }
    }

    // Markers — chronological (guide §5 / §7)
    if (markersApiRef.current) {
      const rows = sortByTime(markers ?? []).map((m) => ({
        time: m.time as Time,
        position: m.position,
        color: m.color,
        shape: m.shape,
        text: m.text,
      }));
      markersApiRef.current.setMarkers(rows);
    }

    // Price lines ENTRY / SL / TP (guide §5B)
    const desired = new Map((priceLines ?? []).map((p) => [p.id, p]));
    for (const [id, line] of [...priceLineRefs.current.entries()]) {
      if (!desired.has(id)) {
        try {
          series.removePriceLine(line);
        } catch {
          /* ignore */
        }
        priceLineRefs.current.delete(id);
      }
    }
    for (const [id, spec] of desired) {
      const style = (spec.lineStyle ?? 2) as LineStyle;
      const existing = priceLineRefs.current.get(id);
      const opts = {
        price: spec.price,
        color: spec.color,
        lineWidth: (spec.lineWidth ?? 1) as 1 | 2 | 3 | 4,
        lineStyle: style,
        axisLabelVisible: true,
        title: spec.title,
      };
      if (existing) {
        existing.applyOptions(opts);
      } else {
        priceLineRefs.current.set(id, series.createPriceLine(opts));
      }
    }

    if (chartRef.current && (candles.length > 0 || equityCurve?.length)) {
      chartRef.current.timeScale().fitContent();
    }
  }, [candles, equityCurve, markers, priceLines, height, seriesKey]);

  return <div ref={ref} className="w-full" style={{ height }} />;
});
