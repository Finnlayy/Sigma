/**
 * TvLightweightChart — §8.2a: Primärchart ist Lightweight Charts (OSS),
 * TV-Widget-Embeds bleiben optionale Ergänzung.
 */
import { useEffect, useRef, memo } from 'react';
import { createChart, ColorType, CandlestickSeries, LineSeries, type IChartApi, type ISeriesApi, type Time } from 'lightweight-charts';
import type { Candle } from '../lib/sigmaApi';

interface Marker { time: number; position: 'aboveBar' | 'belowBar'; color: string; shape: 'arrowUp' | 'arrowDown'; text: string }

interface Props {
  candles: Candle[];
  equityCurve?: Array<{ ts: number; value: number }>;
  markers?: Marker[];
  height?: number;
}

export default memo(function TvLightweightChart({ candles, equityCurve, height = 260 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const equitySeriesRef = useRef<ISeriesApi<"Line"> | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#a1a1aa',
        fontSize: 10,
      },
      grid: {
        vertLines: { color: 'rgba(63,63,70,0.25)' },
        horzLines: { color: 'rgba(63,63,70,0.25)' },
      },
      rightPriceScale: { borderColor: 'rgba(63,63,70,0.5)' },
      timeScale: { borderColor: 'rgba(63,63,70,0.5)', timeVisible: true },
      crosshair: { mode: 0 },
    });
    chartRef.current = chart;

    candleSeriesRef.current = chart.addSeries(CandlestickSeries, {
      upColor: '#10b981', downColor: '#ef4444',
      wickUpColor: '#10b981', wickDownColor: '#ef4444', borderVisible: false,
    });

    equitySeriesRef.current = chart.addSeries(LineSeries, { color: '#38bdf8', lineWidth: 2 });

    const observer = new ResizeObserver(() => chart.applyOptions({ width: ref.current?.clientWidth ?? 300 }));
    observer.observe(ref.current);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      equitySeriesRef.current = null;
    };
  }, []); // Only run once on mount

  useEffect(() => {
    if (chartRef.current) {
      chartRef.current.applyOptions({ height });
    }

    if (candleSeriesRef.current) {
      if (candles.length) {
        candleSeriesRef.current.setData(candles.map((c) => ({
          time: c.ts as Time, open: c.o, high: c.h, low: c.l, close: c.c,
        })));
      } else {
        candleSeriesRef.current.setData([]);
      }
    }

    if (equitySeriesRef.current) {
      if (equityCurve?.length) {
        equitySeriesRef.current.setData(equityCurve.map((p) => ({ time: p.ts as Time, value: p.value })));
      } else {
        equitySeriesRef.current.setData([]);
      }
    }

    if (chartRef.current && (candles.length > 0 || equityCurve?.length)) {
      chartRef.current.timeScale().fitContent();
    }
  }, [candles, equityCurve, height]);

  return <div ref={ref} className="w-full" style={{ height }} />;
});
