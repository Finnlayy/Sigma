/**
 * Sigma dock — FlexLayout semantics with shadcn Resizable + Tabs.
 * Nested rows stack vertically; a row of tabsets splits horizontally.
 */
import { useMemo, useState, type ReactNode } from 'react';
import { Maximize2, Minimize2, X } from 'lucide-react';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { PANEL_REGISTRY, PANEL_TITLES } from './panels';

export type DockLeaf = {
  type: 'tabset';
  id: string;
  weight: number;
  panels: string[];
  active: string;
};

export type DockSplit = {
  type: 'split';
  id: string;
  direction: 'horizontal' | 'vertical';
  weight: number;
  children: DockNode[];
};

export type DockNode = DockLeaf | DockSplit;

type FlexChild = { type: string; weight?: number; children?: FlexChild[]; component?: string };

let _seq = 0;
const uid = (p = 'n') => `${p}-${++_seq}-${Math.random().toString(36).slice(2, 6)}`;

export function fromFlexLayout(node: FlexChild, depth = 0): DockNode {
  if (node.type === 'tabset') {
    const panels = (node.children ?? []).map((c) => c.component).filter(Boolean) as string[];
    return {
      type: 'tabset',
      id: uid('ts'),
      weight: node.weight ?? 50,
      panels,
      active: panels[0] ?? '',
    };
  }
  const kids = (node.children ?? []).map((c) => fromFlexLayout(c, depth + 1));
  const allSplits = kids.length > 0 && kids.every((k) => k.type === 'split');
  return {
    type: 'split',
    id: uid('sp'),
    direction: allSplits ? 'vertical' : 'horizontal',
    weight: node.weight ?? 50,
    children: kids,
  };
}

export function collectPanels(node: DockNode, out = new Set<string>()): Set<string> {
  if (node.type === 'tabset') {
    node.panels.forEach((p) => out.add(p));
  } else {
    node.children.forEach((c) => collectPanels(c, out));
  }
  return out;
}

export function addPanelToActive(node: DockNode, activeId: string, panel: string): DockNode {
  if (node.type === 'tabset') {
    if (node.id !== activeId) return node;
    if (node.panels.includes(panel)) return { ...node, active: panel };
    return { ...node, panels: [...node.panels, panel], active: panel };
  }
  return { ...node, children: node.children.map((c) => addPanelToActive(c, activeId, panel)) };
}

export function closePanel(node: DockNode, tabsetId: string, panel: string): DockNode | null {
  if (node.type === 'tabset') {
    if (node.id !== tabsetId) return node;
    const panels = node.panels.filter((p) => p !== panel);
    if (!panels.length) return null;
    const active = node.active === panel ? panels[0] : node.active;
    return { ...node, panels, active };
  }
  const children = node.children
    .map((c) => closePanel(c, tabsetId, panel))
    .filter((c): c is DockNode => c != null);
  if (!children.length) return null;
  if (children.length === 1) return { ...children[0], weight: node.weight };
  return { ...node, children };
}

export function setActivePanel(node: DockNode, tabsetId: string, panel: string): DockNode {
  if (node.type === 'tabset') {
    if (node.id !== tabsetId) return node;
    return { ...node, active: panel };
  }
  return { ...node, children: node.children.map((c) => setActivePanel(c, tabsetId, panel)) };
}

function firstTabsetId(node: DockNode): string {
  if (node.type === 'tabset') return node.id;
  return firstTabsetId(node.children[0]);
}

function normalizeSizes(children: DockNode[]): number[] {
  const total = children.reduce((s, c) => s + (c.weight || 1), 0) || 1;
  return children.map((c) => Math.max(8, Math.round(((c.weight || 1) / total) * 100)));
}

function DockTabset({
  node,
  maximized,
  onMaximize,
  onActivate,
  onClose,
  onSelect,
}: {
  node: DockLeaf;
  maximized: string | null;
  onMaximize: (id: string | null) => void;
  onActivate: (id: string) => void;
  onClose: (tabsetId: string, panel: string) => void;
  onSelect: (tabsetId: string, panel: string) => void;
}) {
  const active = node.panels.includes(node.active) ? node.active : node.panels[0];
  const isMax = maximized === node.id;

  return (
    <div
      className="flex h-full min-h-0 min-w-0 flex-col bg-card"
      onMouseDown={() => onActivate(node.id)}
    >
      <Tabs
        value={active}
        onValueChange={(v) => { onActivate(node.id); onSelect(node.id, v); }}
        className="flex h-full min-h-0 flex-col gap-0"
      >
        <div className="flex items-center gap-1 border-b border-border bg-muted/40 px-1">
          <TabsList variant="line" className="h-7 min-w-0 flex-1 justify-start overflow-x-auto rounded-none bg-transparent p-0">
            {node.panels.map((p) => (
              <TabsTrigger
                key={p}
                value={p}
                className="h-7 flex-none px-2 text-[10px] font-semibold uppercase tracking-wide"
              >
                {PANEL_TITLES[p] ?? p}
                <span
                  role="button"
                  className="ml-1 rounded p-0.5 hover:bg-destructive/20 hover:text-destructive"
                  onClick={(e) => { e.preventDefault(); e.stopPropagation(); onClose(node.id, p); }}
                  onPointerDown={(e) => e.stopPropagation()}
                >
                  <X className="size-2.5" />
                </span>
              </TabsTrigger>
            ))}
          </TabsList>
          <Button
            variant="ghost"
            size="icon-xs"
            className="text-muted-foreground"
            onClick={() => onMaximize(isMax ? null : node.id)}
            title={isMax ? 'Restore' : 'Maximize'}
          >
            {isMax ? <Minimize2 className="size-3" /> : <Maximize2 className="size-3" />}
          </Button>
        </div>
        {node.panels.map((p) => {
          const Panel = PANEL_REGISTRY[p];
          return (
            <TabsContent key={p} value={p} className="mt-0 min-h-0 flex-1 overflow-hidden data-[state=inactive]:hidden">
              {Panel ? <Panel /> : (
                <div className="p-3 text-xs text-muted-foreground">Unknown panel: {p}</div>
              )}
            </TabsContent>
          );
        })}
      </Tabs>
    </div>
  );
}

function DockBranch({
  node,
  maximized,
  onMaximize,
  onActivate,
  onClose,
  onSelect,
}: {
  node: DockNode;
  maximized: string | null;
  onMaximize: (id: string | null) => void;
  onActivate: (id: string) => void;
  onClose: (tabsetId: string, panel: string) => void;
  onSelect: (tabsetId: string, panel: string) => void;
}) {
  if (node.type === 'tabset') {
    return (
      <DockTabset
        node={node}
        maximized={maximized}
        onMaximize={onMaximize}
        onActivate={onActivate}
        onClose={onClose}
        onSelect={onSelect}
      />
    );
  }

  if (maximized) {
    const hit = node.children.find((c) => containsId(c, maximized));
    if (hit) {
      return (
        <DockBranch
          node={hit}
          maximized={maximized}
          onMaximize={onMaximize}
          onActivate={onActivate}
          onClose={onClose}
          onSelect={onSelect}
        />
      );
    }
  }

  const sizes = normalizeSizes(node.children);
  const items: ReactNode[] = [];
        node.children.forEach((child, i) => {
          if (i > 0) {
            items.push(<ResizableHandle key={`h-${child.id}`} withHandle />);
          }
          items.push(
            <ResizablePanel key={child.id} defaultSize={sizes[i]} minSize={10} className="min-h-0 min-w-0">
              <DockBranch
                node={child}
                maximized={maximized}
                onMaximize={onMaximize}
                onActivate={onActivate}
                onClose={onClose}
                onSelect={onSelect}
              />
            </ResizablePanel>,
          );
        });
        return (
          <ResizablePanelGroup orientation={node.direction} className="h-full min-h-0">
            {items}
          </ResizablePanelGroup>
        );
}

function containsId(node: DockNode, id: string): boolean {
  if (node.id === id) return true;
  if (node.type === 'split') return node.children.some((c) => containsId(c, id));
  return false;
}

export function SigmaDock({
  tree,
  onChange,
  activeTabsetId,
  onActiveTabset,
}: {
  tree: DockNode;
  onChange: (next: DockNode) => void;
  activeTabsetId: string;
  onActiveTabset: (id: string) => void;
}) {
  const [maximized, setMaximized] = useState<string | null>(null);
  const fallbackActive = useMemo(() => firstTabsetId(tree), [tree]);
  const active = activeTabsetId || fallbackActive;

  return (
    <div className={cn('h-full min-h-0 w-full bg-background')}>
      <DockBranch
        node={tree}
        maximized={maximized}
        onMaximize={setMaximized}
        onActivate={onActiveTabset}
        onSelect={(ts, panel) => onChange(setActivePanel(tree, ts, panel))}
        onClose={(ts, panel) => {
          const next = closePanel(tree, ts, panel);
          if (next) onChange(next);
        }}
      />
      <span className="hidden">{active}</span>
    </div>
  );
}
