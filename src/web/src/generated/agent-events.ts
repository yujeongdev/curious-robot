import { z } from "zod";

export const detailedStages = [
  "observe",
  "reason",
  "identify_unknowns",
  "design_experiment",
  "act",
  "measure",
  "system_identification",
  "update_simulation",
  "predict",
  "select_action",
  "execute",
  "verify",
  "learn",
] as const;

export const eventTypes = [
  "run.started", "run.paused", "run.resumed", "run.completed", "run.failed", "run.cancelled", "run.abandoned",
  "stage.entered", "stage.completed", "rationale.published", "tool.started", "tool.completed", "tool.failed",
  "measurement.sampled", "measurement.summary", "belief.updated", "simulation.belief_rebuilt", "simulation.visual_frame",
  "prediction.completed", "action.selected", "action.executed", "verification.completed", "learning.completed",
  "safety.triggered", "evaluation.revealed", "reexecution.completed", "artifact.created",
] as const;

export const vector3Schema = z.tuple([z.number(), z.number(), z.number()]);
export const quaternionSchema = z.tuple([z.number(), z.number(), z.number(), z.number()]);

export const bodyTransformSchema = z.object({
  body_id: z.string(),
  label: z.string(),
  asset_node_id: z.string(),
  position_m: vector3Schema,
  orientation_xyzw: quaternionSchema,
});

export const contactSchema = z.object({
  position_m: vector3Schema,
  normal_world: vector3Schema,
  force_world_n: vector3Schema,
  shape_a_label: z.string().optional(),
  shape_b_label: z.string().optional(),
});

export const vectorVisualSchema = z.object({
  vector_id: z.string(),
  kind: z.enum(["measured_force", "commanded_force", "target", "prediction", "observed"]),
  origin_m: vector3Schema,
  vector: vector3Schema,
  frame: z.string(),
  units: z.string(),
  label: z.string().nullable().optional(),
});

export const visualFramePayloadSchema = z.object({
  frame_id: z.number().int().nonnegative(),
  source_state_sequence: z.number().int().nonnegative(),
  scene: z.enum(["truth", "belief"]),
  world_instance_id: z.string(),
  scenario_id: z.string().nullable().optional(),
  action_id: z.string().nullable().optional(),
  belief_revision: z.number().int().nullable().optional(),
  body_manifest_id: z.string(),
  sim_time_s: z.number(),
  presentation_time_s: z.number(),
  bodies: z.array(bodyTransformSchema),
  contacts: z.array(contactSchema).default([]),
  vectors: z.array(vectorVisualSchema).default([]),
  target_region: z.object({ center_m: vector3Schema, radius_m: z.number().positive() }).optional(),
  camera_hint: z.object({ preset: z.string(), target_m: vector3Schema.nullable().optional() }).nullable().optional(),
});

export const agentEventSchema = z.object({
  event_id: z.string(),
  run_id: z.string(),
  sequence: z.number().int().positive(),
  wall_time: z.string(),
  sim_time_s: z.number().nullable(),
  stage: z.enum(detailedStages).nullable(),
  frame_id: z.number().int().nullable(),
  type: z.enum(eventTypes),
  payload: z.record(z.string(), z.unknown()),
  schema_version: z.string().regex(/^1\./, "Agent Lab supports event schema 1.x"),
  iteration_id: z.number().int().nonnegative(),
  stage_instance_id: z.string().nullable(),
});

export type DetailedStage = (typeof detailedStages)[number];
export type AgentEventType = (typeof eventTypes)[number];
export type AgentEvent = z.infer<typeof agentEventSchema>;
export type VisualFramePayload = z.infer<typeof visualFramePayloadSchema>;
export type BodyTransform = z.infer<typeof bodyTransformSchema>;

export function parseAgentEvent(input: unknown): AgentEvent {
  const event = agentEventSchema.parse(input);
  if (event.type === "simulation.visual_frame") visualFramePayloadSchema.parse(event.payload);
  return event;
}
