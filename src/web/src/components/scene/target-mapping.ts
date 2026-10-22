import type { VisualFramePayload } from "../../generated/agent-events";

export type TargetRegion = NonNullable<VisualFramePayload["target_region"]>;

export type TargetMarker =
  | { kind: "shape"; parts: NonNullable<TargetRegion["geometry"]>["parts"] }
  | { kind: "ring"; radiusM: number };

/** Select presentation geometry recorded by Newton; never infer browser physics. */
export function resolveTargetMarker(target: TargetRegion): TargetMarker {
  if (target.geometry) return { kind: "shape", parts: target.geometry.parts };
  return { kind: "ring", radiusM: target.radius_m };
}
