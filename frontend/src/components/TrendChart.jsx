import { useMemo, useState } from "react";
import { num } from "../lib/format.js";

/**
 * People-count trend (average and peak per bucket) plus entries per bucket.
 * Plain SVG, no chart library: one series is a filled area, the peak a line.
 */
export default function TrendChart({ points, timeZone }) {
  const [hover, setHover] = useState(null);
  const width = 720;
  const height = 200;
  const pad = { l: 36, r: 12, t: 12, b: 26 };

  const geo = useMemo(() => {
    if (!points || points.length === 0) return null;
    const maxY = Math.max(1, ...points.map((p) => p.max_count));
    const n = points.length;
    const x = (i) => pad.l + (n === 1 ? (width - pad.l - pad.r) / 2 : (i * (width - pad.l - pad.r)) / (n - 1));
    const y = (v) => pad.t + (height - pad.t - pad.b) * (1 - v / maxY);
    const avg = points.map((p, i) => [x(i), y(p.avg_count)]);
    const area = `M${avg[0][0]},${y(0)} ` + avg.map(([a, b]) => `L${a},${b}`).join(" ") + ` L${avg[n - 1][0]},${y(0)} Z`;
    const peak = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.max_count)}`).join(" ");
    const ticks = [0, Math.round(maxY / 2), maxY];
    const labelEvery = Math.max(1, Math.ceil(n / 6));
    return { x, y, area, peak, ticks, labelEvery, avg };
  }, [points]);

  if (!geo) {
    return <p className="muted empty-chart">No history yet. Counts are stored once per minute while AI is running.</p>;
  }
  const fmt = (t) =>
    new Date(t).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", timeZone });
  const h = hover !== null ? points[hover] : null;

  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="People count trend"
           onMouseLeave={() => setHover(null)}>
        {geo.ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={width - pad.r} y1={geo.y(t)} y2={geo.y(t)} className="grid" />
            <text x={pad.l - 6} y={geo.y(t) + 4} textAnchor="end" className="axis">{t}</text>
          </g>
        ))}
        <path d={geo.area} className="area" />
        <path d={geo.peak} className="peak" />
        {points.map((p, i) =>
          i % geo.labelEvery === 0 || i === points.length - 1 ? (
            <text key={p.bucket_start} x={geo.x(i)} y={height - 6} className="axis"
                  textAnchor={points.length > 1 && i === 0 ? "start" : points.length > 1 && i === points.length - 1 ? "end" : "middle"}>
              {fmt(p.bucket_start)}
            </text>
          ) : null,
        )}
        {points.map((p, i) => (
          <rect key={`h${p.bucket_start}`} x={geo.x(i) - 8} y={pad.t} width={16} height={height - pad.t - pad.b}
                fill="transparent" onMouseEnter={() => setHover(i)} />
        ))}
        {h && <line x1={geo.x(hover)} x2={geo.x(hover)} y1={pad.t} y2={height - pad.b} className="cursor" />}
      </svg>
      <div className="chart-legend">
        <span><i className="sw sw-area" /> average people</span>
        <span><i className="sw sw-peak" /> peak</span>
        {h && (
          <span className="chart-readout">
            {fmt(h.bucket_start)} · avg {num(h.avg_count, 1)} · peak {h.max_count} · in {h.entries} · out {h.exits}
          </span>
        )}
      </div>
    </div>
  );
}
