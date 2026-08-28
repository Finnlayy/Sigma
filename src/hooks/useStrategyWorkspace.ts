import { useCallback, useEffect, useState } from 'react';
import type { TradingStrategy } from '../types';
import { safeFetchJson } from '../lib/api';

export function useStrategyWorkspace() {
  const [strategies, setStrategies] = useState<TradingStrategy[]>([]);
  const [selected, setSelected] = useState<TradingStrategy | null>(null);

  const reload = useCallback(async () => {
    const data = await safeFetchJson<TradingStrategy[]>('/api/strategies');
    if (!data) return;
    setStrategies(data);
    setSelected((prev) => {
      if (prev) return data.find((s) => s.id === prev.id) || prev;
      return data[0] ?? null;
    });
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  const select = useCallback((s: TradingStrategy) => setSelected(s), []);

  const update = useCallback(async (id: string, updates: Partial<TradingStrategy>) => {
    const res = await fetch(`/api/strategies/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    });
    if (!res.ok) return;
    const updated = await res.json();
    setStrategies((prev) => prev.map((s) => (s.id === id ? updated : s)));
    setSelected((prev) => (prev?.id === id ? updated : prev));
  }, []);

  const create = useCallback(async (strategy: Partial<TradingStrategy>) => {
    const res = await fetch('/api/strategies', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(strategy),
    });
    if (!res.ok) return;
    const created = await res.json();
    setStrategies((prev) => [...prev, created]);
    setSelected(created);
  }, []);

  const remove = useCallback(async (id: string) => {
    const res = await fetch(`/api/strategies/${id}`, { method: 'DELETE' });
    if (!res.ok) return;
    setStrategies((prev) => prev.filter((s) => s.id !== id));
    setSelected((prev) => (prev?.id === id ? null : prev));
  }, []);

  const archive = useCallback(async (id: string) => {
    const res = await fetch(`/api/strategies/${id}/archive`, { method: 'POST' });
    if (!res.ok) return;
    const data = await res.json();
    setStrategies((prev) => prev.map((s) => (s.id === id ? data.strategy : s)));
    setSelected((prev) => (prev?.id === id ? data.strategy : prev));
  }, []);

  const restore = useCallback(async (id: string) => {
    const res = await fetch(`/api/strategies/${id}/restore`, { method: 'POST' });
    if (!res.ok) return;
    const data = await res.json();
    setStrategies((prev) => prev.map((s) => (s.id === id ? data.strategy : s)));
    setSelected((prev) => (prev?.id === id ? data.strategy : prev));
  }, []);

  const toggleRun = useCallback(async (id: string, action: 'start' | 'stop', mode?: 'paper' | 'live') => {
    const res = await fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, action, mode }),
    });
    if (!res.ok) return;
    const updated = await res.json();
    setStrategies((prev) => prev.map((s) => (s.id === id ? updated : s)));
    setSelected((prev) => (prev?.id === id ? updated : prev));
  }, []);

  return {
    strategies, selected, select, reload, update, create, remove, archive, restore, toggleRun,
  };
}
