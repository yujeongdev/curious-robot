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
  "action.graph_compiled", "action.node_transitioned",
  "safety.triggered", "evaluation.revealed", "reexecution.completed", "artifact.created",
] as const;

const sha256IdSchema = z.string().regex(/^sha256:[0-9a-f]{64}$/);

export const actionGraphNodeSchema = z.object({
  node_id: z.string().regex(/^[a-z][a-z0-9_]*$/),
  kind: z.enum(["observe", "guard", "motion", "verify", "safe_stop", "terminal"]),
  authority: z.enum(["hardware", "reality", "supervisor"]),
  executor: z.enum(["deterministic_transport", "perception", "supervisor"]),
  control_mode: z.enum(["joint_position", "cartesian_pose", "cartesian_velocity", "cartesian_impedance", "joint_torque"]).nullable().optional(),
  required_capabilities: z.array(z.string()).default([]),
  timeout_s: z.number().positive(),
  checkpoint_safe: z.boolean().default(false),
  parameters: z.record(z.string(), z.union([z.string(), z.number(), z.boolean()])),
});

export const actionGraphEdgeSchema = z.object({
  source_node_id: z.string(),
  target_node_id: z.string(),
  guard: z.enum(["on_success", "on_failure", "on_safety_trip", "on_goal_miss"]),
});

export const actionGraphSchema = z.object({
  graph_schema_version: z.literal("action-graph-v1"),
  compiler_version: z.literal("motion-plan-compiler-v1"),
  graph_name: z.string(),
  task_id: z.string(),
  task_version: z.string(),
  object_id: z.string(),
  object_archetype_version: z.string(),
  geometry_hash: sha256IdSchema,
  calibration_id: sha256IdSchema,
  protocol_binding_id: sha256IdSchema,
  entry_node_id: z.string(),
  terminal_node_ids: z.array(z.string()).min(1),
  wall_clock_deadline_s: z.number().positive(),
  nodes: z.array(actionGraphNodeSchema).min(1),
  edges: z.array(actionGraphEdgeSchema),
}).superRefine((graph, context) => {
  const nodeIds = new Set(graph.nodes.map((node) => node.node_id));
  if (!nodeIds.has(graph.entry_node_id)) context.addIssue({ code: "custom", message: "entry node must exist", path: ["entry_node_id"] });
  for (const nodeId of graph.terminal_node_ids) {
    if (!nodeIds.has(nodeId)) context.addIssue({ code: "custom", message: "terminal node must exist", path: ["terminal_node_ids"] });
  }
  for (const [index, edge] of graph.edges.entries()) {
    if (!nodeIds.has(edge.source_node_id) || !nodeIds.has(edge.target_node_id)) {
      context.addIssue({ code: "custom", message: "edge nodes must exist", path: ["edges", index] });
    }
  }
});

export const actionGraphCompiledPayloadSchema = z.object({
  graph_id: sha256IdSchema,
  motion_plan_id: sha256IdSchema.nullable().optional(),
  deployment_bundle_id: sha256IdSchema.nullable().optional(),
  graph: actionGraphSchema,
}).superRefine((payload, context) => {
  if (payload.deployment_bundle_id && !payload.motion_plan_id) {
    context.addIssue({ code: "custom", message: "deployment bundle ID requires a motion plan ID", path: ["deployment_bundle_id"] });
  }
});

export const actionNodeTransitionPayloadSchema = z.object({
  graph_id: sha256IdSchema,
  node_id: z.string(),
  state: z.enum(["entered", "completed", "failed", "safety_trip", "goal_miss"]),
  outcome: z.enum(["success", "failure", "safety_trip", "goal_miss"]).nullable().optional(),
  evidence_ids: z.array(z.string()).default([]),
}).superRefine((transition, context) => {
  const expected = {
    completed: "success",
    failed: "failure",
    safety_trip: "safety_trip",
    goal_miss: "goal_miss",
  } as const;
  if (transition.state === "entered" && transition.outcome != null) {
    context.addIssue({ code: "custom", message: "entered action node cannot declare a terminal outcome", path: ["outcome"] });
  }
  if (transition.state !== "entered" && transition.outcome !== expected[transition.state]) {
    context.addIssue({ code: "custom", message: "action node transition state and outcome do not match", path: ["outcome"] });
  }
});

export const vector3Schema = z.tuple([z.number(), z.number(), z.number()]);
export const quaternionSchema = z.tuple([z.number(), z.number(), z.number(), z.number()]);

export const bodyTransformSchema = z.object({
  body_id: z.string(),
  label: z.string(),
  asset_node_id: z.string(),
  position_m: vector3Schema,
  orientation_xyzw: quaternionSchema,
});

export const visualRigidBoxPartSchema = z.object({
  part_id: z.string(),
  center_object_m: vector3Schema,
  half_extents_m: vector3Schema.refine((value) => value.every((item) => Number.isFinite(item) && item > 0), "half extents must be finite and positive"),
  orientation_object_xyzw: quaternionSchema.default([0, 0, 0, 1]).refine((value) => Math.abs(Math.hypot(...value) - 1) <= 1e-6, "orientation must be a unit quaternion"),
});

export const objectGeometryVisualSchema = z.object({
  archetype_id: z.string(),
  archetype_version: z.string(),
  geometry_hash: z.string().regex(/^sha256:[0-9a-f]{64}$/),
  body_id: z.string(),
  parts: z.array(visualRigidBoxPartSchema).min(1),
});

export const targetGeometryVisualSchema = objectGeometryVisualSchema.omit({ body_id: true });

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
  object_geometry: objectGeometryVisualSchema.nullable().optional(),
  tool_tip_radius_m: z.number().positive().nullable().optional(),
  sim_time_s: z.number(),
  presentation_time_s: z.number(),
  bodies: z.array(bodyTransformSchema),
  contacts: z.array(contactSchema).default([]),
  vectors: z.array(vectorVisualSchema).default([]),
  target_region: z.object({
    center_m: vector3Schema,
    radius_m: z.number().positive(),
    yaw_rad: z.number().default(0),
    yaw_tolerance_rad: z.number().positive().nullable().optional(),
    geometry: targetGeometryVisualSchema.nullable().optional(),
  }).optional(),
  camera_hint: z.object({ preset: z.string(), target_m: vector3Schema.nullable().optional() }).nullable().optional(),
}).superRefine((frame, context) => {
  if (frame.object_geometry && !frame.bodies.some((body) => body.body_id === frame.object_geometry?.body_id)) {
    context.addIssue({ code: "custom", message: "object geometry body must exist in frame", path: ["object_geometry", "body_id"] });
  }
  const targetGeometry = frame.target_region?.geometry;
  if (targetGeometry) {
    const objectGeometry = frame.object_geometry;
    const sameGeometry = objectGeometry
      && targetGeometry.archetype_id === objectGeometry.archetype_id
      && targetGeometry.archetype_version === objectGeometry.archetype_version
      && targetGeometry.geometry_hash === objectGeometry.geometry_hash
      && JSON.stringify(targetGeometry.parts) === JSON.stringify(objectGeometry.parts);
    if (!sameGeometry) {
      context.addIssue({
        code: "custom",
        message: "target geometry must exactly match recorded object geometry",
        path: ["target_region", "geometry"],
      });
    }
  }
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
export type ActionGraphSnapshot = z.infer<typeof actionGraphSchema>;
export type ActionGraphNode = z.infer<typeof actionGraphNodeSchema>;
export type ActionGraphNodeTransition = z.infer<typeof actionNodeTransitionPayloadSchema>;

export function parseAgentEvent(input: unknown): AgentEvent {
  const event = agentEventSchema.parse(input);
  if (event.type === "simulation.visual_frame") visualFramePayloadSchema.parse(event.payload);
  if (event.type === "action.graph_compiled") actionGraphCompiledPayloadSchema.parse(event.payload);
  if (event.type === "action.node_transitioned") actionNodeTransitionPayloadSchema.parse(event.payload);
  return event;
}
