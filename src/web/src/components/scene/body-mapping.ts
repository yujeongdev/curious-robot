import type { BodyTransform } from "../../generated/agent-events";

export function isFrankaBody(body: BodyTransform): boolean {
  return /(?:^|[/_])(fr3|franka|panda)(?:[/_]|$)/i.test(`${body.body_id} ${body.label}`);
}

export function findCubeBody(bodies: readonly BodyTransform[]): BodyTransform | undefined {
  return bodies.find((body) => body.body_id === "unknown_cube" || body.body_id === "cube");
}
