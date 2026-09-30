"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

import { formatClock } from "@/lib/format";

export interface Point {
  t: number;
  v: number | null;
}

const MARGIN = { top: 8, right: 10, bottom: 18, left: 34 };

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 2.5, 5, 10]) {
    if (value <= step * magnitude) return step * magnitude;
  }
  return 10 * magnitude;
}

function compactTick(value: number): string {
  if (value >= 1_000_000) return `${+(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${+(value / 1_000).toFixed(1)}K`;
  return `${Math.round(value)}`;
}

/**
 * Single-series line chart (2px line, 10% area wash) with a crosshair tooltip. Several of
 * these are laid out as small multiples instead of one multi-series chart, so every chart
 * has one unit on one axis and no legend is needed (the card title names the series).
 */
export function LineChart({
  points,
  label,
  format,
  yMax,
  height = 110,
}: {
  points: Point[];
  label: string;
  format: (value: number) => string;
  yMax?: number;
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(320);
  const [active, setActive] = useState<number | null>(null);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.max(160, Math.floor(entry.contentRect.width)));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const valid = useMemo(() => points.filter((p): p is { t: number; v: number } => p.v !== null), [points]);
  const plotWidth = width - MARGIN.left - MARGIN.right;
  const plotHeight = height - MARGIN.top - MARGIN.bottom;

  const geometry = useMemo(() => {
    if (valid.length === 0) return null;
    const first = points[0]?.t ?? 0;
    const last = points[points.length - 1]?.t ?? first;
    const [t0, t1] = first === last ? [first - 30_000, last + 30_000] : [first, last];
    const top = yMax ?? niceMax(Math.max(...valid.map((p) => p.v)) * 1.1);
    const x = (t: number) => MARGIN.left + ((t - t0) / (t1 - t0)) * plotWidth;
    const y = (v: number) => MARGIN.top + plotHeight - (Math.min(v, top) / top) * plotHeight;
    const segments: { x: number; y: number }[][] = [];
    let current: { x: number; y: number }[] = [];
    for (const point of points) {
      if (point.v === null) {
        if (current.length) segments.push(current);
        current = [];
      } else {
        current.push({ x: x(point.t), y: y(point.v) });
      }
    }
    if (current.length) segments.push(current);
    return { top, x, y, segments, baseline: MARGIN.top + plotHeight };
  }, [points, valid, yMax, plotWidth, plotHeight]);

  if (!geometry) {
    return (
      <div className="chart-empty" style={{ height }}>
        No data yet
      </div>
    );
  }

  const { top, x, y, segments, baseline } = geometry;
  const line = (seg: { x: number; y: number }[]) => seg.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join("");
  const area = (seg: { x: number; y: number }[]) =>
    seg.length > 1 ? `${line(seg)}L${seg[seg.length - 1]!.x.toFixed(1)},${baseline}L${seg[0]!.x.toFixed(1)},${baseline}Z` : "";
  const ticks = [0, top / 2, top];
  const lastIndex = points.reduce((acc, p, i) => (p.v !== null ? i : acc), -1);
  const shown = active !== null && points[active]?.v !== null ? active : null;
  const shownPoint = shown !== null ? points[shown] : undefined;

  const nearestIndex = (clientX: number) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return null;
    const px = clientX - rect.left;
    let best: number | null = null;
    let bestDistance = Infinity;
    points.forEach((p, i) => {
      if (p.v === null) return;
      const distance = Math.abs(x(p.t) - px);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = i;
      }
    });
    return best;
  };

  const onPointerMove = (event: PointerEvent<SVGRectElement>) => setActive(nearestIndex(event.clientX));

  const onKeyDown = (event: KeyboardEvent<SVGRectElement>) => {
    const indices = points.map((p, i) => (p.v !== null ? i : -1)).filter((i) => i >= 0);
    if (!indices.length) return;
    const position = active === null ? indices.length - 1 : Math.max(0, indices.indexOf(active));
    const moves: Record<string, number> = {
      ArrowLeft: Math.max(0, position - 1),
      ArrowRight: Math.min(indices.length - 1, position + 1),
      Home: 0,
      End: indices.length - 1,
    };
    if (event.key in moves) {
      event.preventDefault();
      setActive(indices[moves[event.key]!] ?? null);
    } else if (event.key === "Escape") {
      setActive(null);
    }
  };

  const tooltipLeft = shownPoint ? Math.min(Math.max(x(shownPoint.t) - 60, 0), width - 130) : 0;

  return (
    <div className="chart" ref={containerRef}>
      <svg width={width} height={height} role="img" aria-label={`${label} over time`}>
        {ticks.map((tick) => (
          <g key={tick}>
            <line
              className={tick === 0 ? "baseline" : "gridline"}
              x1={MARGIN.left}
              x2={width - MARGIN.right}
              y1={y(tick)}
              y2={y(tick)}
            />
            <text className="tick" x={MARGIN.left - 6} y={y(tick)} dy="0.32em" textAnchor="end">
              {compactTick(tick)}
            </text>
          </g>
        ))}
        <text className="tick" x={MARGIN.left} y={height - 4} textAnchor="start">
          {formatClock(new Date(points[0]!.t))}
        </text>
        <text className="tick" x={width - MARGIN.right} y={height - 4} textAnchor="end">
          {formatClock(new Date(points[points.length - 1]!.t))}
        </text>
        {segments.map((seg, i) => (
          <path key={`a${i}`} className="series-area" d={area(seg)} />
        ))}
        {segments.map((seg, i) => (
          <path key={`l${i}`} className="series-line" d={line(seg)} />
        ))}
        {shownPoint ? (
          <line className="crosshair" x1={x(shownPoint.t)} x2={x(shownPoint.t)} y1={MARGIN.top} y2={baseline} />
        ) : null}
        {lastIndex >= 0 && shown === null ? (
          <circle className="marker" cx={x(points[lastIndex]!.t)} cy={y(points[lastIndex]!.v!)} r={4} />
        ) : null}
        {shownPoint && shownPoint.v !== null ? (
          <circle className="marker" cx={x(shownPoint.t)} cy={y(shownPoint.v)} r={4} />
        ) : null}
        <rect
          className="hit"
          x={MARGIN.left}
          y={0}
          width={plotWidth}
          height={height}
          tabIndex={0}
          aria-label={`${label} chart. Use the left and right arrow keys to read values.`}
          onPointerMove={onPointerMove}
          onPointerLeave={() => setActive(null)}
          onFocus={() => setActive(lastIndex >= 0 ? lastIndex : null)}
          onBlur={() => setActive(null)}
          onKeyDown={onKeyDown}
        />
      </svg>
      {shownPoint && shownPoint.v !== null ? (
        <div className="chart-tooltip" style={{ left: tooltipLeft }} role="status">
          <strong>
            <i className="key" aria-hidden="true" />
            {format(shownPoint.v)}
          </strong>
          <span>
            {label} · {formatClock(new Date(shownPoint.t))}
          </span>
        </div>
      ) : null}
    </div>
  );
}
