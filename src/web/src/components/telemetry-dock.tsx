import { Activity, ChevronsDownUp, Target } from "lucide-react";
import { useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from "recharts";
import type { RunState } from "../state/run-reducer";
import { formatValue } from "../lib/utils";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";

export function TelemetryDock({ run }: { run: RunState }) {
  const [collapsed, setCollapsed] = useState(false);
  const latest = run.measurements.at(-1);
  const beliefRows = Object.values(run.beliefs);
  return (
    <section className={`telemetry ${collapsed ? "telemetry--collapsed" : ""}`} aria-labelledby="telemetry-title">
      <header className="region-header telemetry-header">
        <div><span className="eyebrow">Synchronized evidence</span><h2 id="telemetry-title">Telemetry</h2></div>
        <div className="telemetry-live-values">
          <span><Activity /> measured <strong className="mono">{formatValue(latest?.measuredForceN, 2)} N</strong></span>
          <span><Target /> position <strong className="mono">{formatValue(latest?.objectPositionM, 3)} m</strong></span>
          <Button size="compact" variant="ghost" onClick={() => setCollapsed((value) => !value)} aria-expanded={!collapsed}>
            <ChevronsDownUp /> {collapsed ? "Expand" : "Collapse"}
          </Button>
        </div>
      </header>
      {!collapsed && (
        <div className="telemetry-grid">
          <div className="chart-panel" aria-label="Measured versus desired force chart">
            <div className="chart-title"><strong>Reality contact force</strong><span><i className="legend-force" /> measured <i className="legend-command" /> desired</span></div>
            {run.measurements.length === 0 ? <div className="chart-empty">Waiting for the friction experiment · N</div> : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={[...run.measurements]} margin={{ top: 8, right: 12, bottom: 0, left: -18 }}>
                  <CartesianGrid stroke="#2a3642" vertical={false} />
                  <XAxis dataKey="simTimeS" tick={{ fill: "#748391", fontSize: 12 }} tickFormatter={(v) => `${v}s`} />
                  <YAxis tick={{ fill: "#748391", fontSize: 12 }} />
                  <ChartTooltip contentStyle={{ background: "#17212b", border: "1px solid #2a3642", borderRadius: 4 }} />
                  <Line type="linear" dataKey="measuredForceN" stroke="#39d0df" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
                  <Line type="linear" dataKey="desiredForceN" stroke="#f2b84b" strokeWidth={2} strokeDasharray="6 4" dot={false} isAnimationActive={false} />
                  {latest && <ReferenceLine x={latest.simTimeS} stroke="#f1f5f7" strokeOpacity={0.55} />}
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
          <div className="belief-panel">
            <div className="chart-title"><strong>Posterior → Twin parameters</strong><Badge>REV {Math.max(0, ...beliefRows.map((belief) => belief.revision))}</Badge></div>
            {beliefRows.length === 0 ? <div className="chart-empty">Broad priors · waiting for measurements</div> : beliefRows.map((belief) => (
              <div className="belief-row" key={belief.parameter}>
                <span>{belief.parameter.replaceAll("_", " ")}</span>
                <strong className="mono">{belief.mean.toFixed(3)} ± {belief.std.toFixed(3)} {belief.unit}</strong>
                <div className="uncertainty-track"><span style={{ left: `${belief.interval95[0] * 80}%`, width: `${Math.max(4, (belief.interval95[1] - belief.interval95[0]) * 80)}%` }} /></div>
                <small className="mono">95% [{belief.interval95[0].toFixed(2)}, {belief.interval95[1].toFixed(2)}]</small>
              </div>
            ))}
          </div>
          <div className="outcome-panel">
            <div className="chart-title"><strong>Twin prediction ↔ Reality</strong></div>
            {run.verification ? (
              <div className={run.verification.success ? "outcome-success" : "outcome-failed"}>
                <span>{run.verification.success ? "✓ VERIFIED" : "× MISMATCH"}</span>
                <strong className="mono">{formatValue(run.verification.residualM, 3)} m</strong>
                <small>residual · tolerance {formatValue(run.verification.toleranceM, 3)} m</small>
              </div>
            ) : <div className="chart-empty">Prediction and observation align here after execution.</div>}
          </div>
        </div>
      )}
    </section>
  );
}
