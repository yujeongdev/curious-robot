import type { BodyTransform, VisualFramePayload } from "../../generated/agent-events";

export interface ObjectVisualBinding {
  body: BodyTransform;
  archetypeId: string;
  parts: NonNullable<VisualFramePayload["object_geometry"]>["parts"];
}

const LEGACY_CUBE_PARTS: ObjectVisualBinding["parts"] = [{
  part_id: "legacy-cube",
  center_object_m: [0, 0, 0],
  half_extents_m: [0.025, 0.025, 0.025],
  orientation_object_xyzw: [0, 0, 0, 1],
}];

export function isFrankaBody(body: BodyTransform): boolean {
  return /(?:^|[/_])(fr3|franka|panda)(?:[/_]|$)/i.test(`${body.body_id} ${body.label}`);
}

export function findCubeBody(bodies: readonly BodyTransform[]): BodyTransform | undefined {
  return bodies.find((body) => body.body_id === "unknown_cube" || body.body_id === "cube");
}

export function findObjectBody(
  bodies: readonly BodyTransform[],
  bodyId?: string,
): BodyTransform | undefined {
  if (bodyId) {
    const exact = bodies.find((body) => body.body_id === bodyId || body.label === bodyId);
    return exact;
  }
  return bodies.find((body) => /(?:^|__)unknown_(?:cube|t_bar)$/.test(body.body_id))
    ?? findCubeBody(bodies);
}

export function resolveObjectVisual(frame: VisualFramePayload): ObjectVisualBinding | undefined {
  const geometry = frame.object_geometry;
  const body = findObjectBody(frame.bodies, geometry?.body_id);
  if (!body) return undefined;
  if (geometry) return { body, archetypeId: geometry.archetype_id, parts: geometry.parts };
  if (findCubeBody([body])) return { body, archetypeId: "cube", parts: LEGACY_CUBE_PARTS };
  return undefined;
}
