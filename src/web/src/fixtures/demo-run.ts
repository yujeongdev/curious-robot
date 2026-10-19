import type { AgentEvent, DetailedStage } from "../generated/agent-events";

const runId = "demo-7-agent-lab";
const started = new Date("2026-10-20T09:00:00.000Z");
let sequence = 0;

function makeEvent(
  type: AgentEvent["type"],
  stage: DetailedStage | null,
  payload: Record<string, unknown>,
  options: { sim?: number; frame?: number; iteration?: number; instance?: string | null } = {},
): AgentEvent {
  sequence += 1;
  return {
    event_id: `demo-event-${sequence}`,
    run_id: runId,
    sequence,
    wall_time: new Date(started.getTime() + sequence * 320).toISOString(),
    sim_time_s: options.sim ?? sequence * 0.12,
    stage,
    frame_id: options.frame ?? null,
    type,
    payload,
    schema_version: "1.0.0",
    iteration_id: options.iteration ?? 0,
    stage_instance_id: options.instance ?? (stage ? `${stage}-${options.iteration ?? 0}` : null),
  };
}

const robotBodies = (cubeX: number, armOffset = 0) => [
  { body_id: "panda_link0", label: "Franka base", asset_node_id: "panda_link0", position_m: [0, 0, 0.32], orientation_xyzw: [0, 0, 0, 1] },
  { body_id: "panda_link1", label: "Shoulder", asset_node_id: "panda_link1", position_m: [0, 0, 0.52], orientation_xyzw: [0, 0, 0, 1] },
  { body_id: "panda_link2", label: "Upper arm", asset_node_id: "panda_link2", position_m: [0.08 + armOffset, 0, 0.72], orientation_xyzw: [0, 0.18, 0, 0.984] },
  { body_id: "panda_link3", label: "Forearm", asset_node_id: "panda_link3", position_m: [0.27 + armOffset, 0, 0.82], orientation_xyzw: [0, 0.35, 0, 0.937] },
  { body_id: "panda_hand", label: "End effector", asset_node_id: "panda_hand", position_m: [0.47 + armOffset, 0, 0.64], orientation_xyzw: [0, 0.7, 0, 0.714] },
  { body_id: "cube", label: "Unknown cube", asset_node_id: "cube", position_m: [cubeX, 0, 0.055], orientation_xyzw: [0, 0, 0, 1] },
] as const;

function frame(frameId: number, stage: DetailedStage, cubeX: number, forceN = 0, scene: "truth" | "belief" = "truth") {
  return makeEvent("simulation.visual_frame", stage, {
    frame_id: frameId,
    source_state_sequence: sequence,
    scene,
    world_instance_id: `${scene}-world-demo`,
    belief_revision: scene === "belief" ? 2 : null,
    body_manifest_id: scene === "truth" ? "franka-cube-v1" : "cube-rollout-v1",
    sim_time_s: frameId * 0.2,
    presentation_time_s: frameId * 0.2,
    bodies: robotBodies(cubeX, Math.min(0.12, forceN * 0.012)),
    contacts: forceN > 0 ? [{ position_m: [cubeX - 0.055, 0, 0.055], normal_world: [-1, 0, 0], force_world_n: [forceN, 0, 0], shape_a_label: "panda_hand", shape_b_label: "cube" }] : [],
    vectors: forceN > 0 ? [{ vector_id: `force-${frameId}`, kind: "measured_force", origin_m: [cubeX - 0.055, 0, 0.07], vector: [forceN, 0, 0], frame: "world", units: "N", label: "measured contact force" }] : [],
    target_region: { center_m: [0.62, 0, 0.005], radius_m: 0.09 },
    camera_hint: { preset: forceN > 0 ? "experiment" : "overview", target_m: [cubeX, 0, 0.05] },
  }, { sim: frameId * 0.2, frame: frameId });
}

function enter(stage: DetailedStage, iteration = 0, detail?: string) {
  return makeEvent("stage.entered", stage, { detail: detail ?? stage.replaceAll("_", " "), iteration }, { iteration });
}

function complete(stage: DetailedStage, iteration = 0, outcome = "completed") {
  return makeEvent("stage.completed", stage, { outcome, duration_s: 1.8, evidence_ids: [] }, { iteration });
}

export const demoEvents: AgentEvent[] = [
  makeEvent("run.started", null, { state: "preparing", preset: "discovery-demo-v1", seed: 7, device: "cuda:0", surface_commitment: "sha256:6df…a12" }, { sim: 0 }),
  enter("observe"),
  makeEvent("rationale.published", "observe", { summary: "Geometry is known. Mass and effective cube–surface friction remain uncertain.", evidence_ids: [], considered_option_ids: [], selected_option_id: "inspect-scene" }),
  frame(1, "observe", 0.28),
  complete("observe"),
  enter("reason"),
  makeEvent("rationale.published", "reason", { summary: "Push success is most sensitive to breakaway friction; measuring it has the highest expected utility.", evidence_ids: ["scene-1"], considered_option_ids: ["weigh", "static-friction", "dynamic-friction"], selected_option_id: "static-friction" }),
  complete("reason"),
  enter("identify_unknowns"),
  makeEvent("belief.updated", "identify_unknowns", { revision: 1, parameter_name: "effective_static_friction", unit: "μ", prior: { mean: 0.55, std: 0.20, interval_95: [0.2, 0.9] }, posterior: { mean: 0.55, std: 0.20, interval_95: [0.2, 0.9] }, method: "bounded prior", evidence_ids: [], mismatch_notes: [] }),
  complete("identify_unknowns"),
  enter("design_experiment"),
  makeEvent("rationale.published", "design_experiment", { summary: "A bounded horizontal force ramp separates low- and high-friction hypotheses with low tip risk.", evidence_ids: ["belief-1"], considered_option_ids: ["lift", "force-ramp"], selected_option_id: "force-ramp" }),
  makeEvent("tool.started", "design_experiment", { tool_call_id: "tool-probe-1", tool_name: "probe_static_friction", arguments: { max_force_n: 5, increment_n: 0.25, displacement_threshold_m: 0.003 } }),
  complete("design_experiment"),
  enter("act", 1),
  frame(2, "act", 0.28, 0.8),
  complete("act", 1),
  enter("measure", 1, "Measure static friction"),
  makeEvent("measurement.sampled", "measure", { evidence_id: "force-1", sensor_kind: "virtual_wrench", producer: "contact_impulse_sensor", frame: "world", units: "N", values: { measured_force_n: 1.1, desired_force_n: 1.25, object_position_m: 0.28, object_velocity_mps: 0 }, validity: "valid" }, { iteration: 1, sim: 2.0 }),
  frame(3, "measure", 0.28, 1.1),
  makeEvent("measurement.sampled", "measure", { evidence_id: "force-2", sensor_kind: "virtual_wrench", producer: "contact_impulse_sensor", frame: "world", units: "N", values: { measured_force_n: 2.2, desired_force_n: 2.25, object_position_m: 0.281, object_velocity_mps: 0.006 }, validity: "valid" }, { iteration: 1, sim: 2.4 }),
  frame(4, "measure", 0.281, 2.2),
  makeEvent("measurement.sampled", "measure", { evidence_id: "force-3", sensor_kind: "virtual_wrench", producer: "contact_impulse_sensor", frame: "world", units: "N", values: { measured_force_n: 2.53, desired_force_n: 2.75, object_position_m: 0.292, object_velocity_mps: 0.041 }, validity: "valid" }, { iteration: 1, sim: 2.8 }),
  frame(5, "measure", 0.292, 2.53),
  makeEvent("measurement.summary", "measure", { evidence_id: "breakaway-summary", accepted_count: 18, rejected_count: 1, aggregates: { breakaway_force_n: 2.53, displacement_m: 0.012 }, quality_flags: ["clear_breakaway"] }),
  makeEvent("tool.completed", "measure", { tool_call_id: "tool-probe-1", tool_name: "probe_static_friction", duration_s: 2.8, result: { breakaway_interval_n: [2.3, 2.75], evidence_id: "breakaway-summary" } }),
  complete("measure", 1),
  enter("system_identification", 1),
  makeEvent("belief.updated", "system_identification", { revision: 2, parameter_name: "effective_static_friction", unit: "μ", prior: { mean: 0.55, std: 0.20, interval_95: [0.2, 0.9] }, posterior: { mean: 0.83, std: 0.035, interval_95: [0.77, 0.89] }, method: "censored breakaway interval", evidence_ids: ["breakaway-summary"], mismatch_notes: [] }),
  makeEvent("belief.updated", "system_identification", { revision: 2, parameter_name: "effective_dynamic_friction", unit: "μ", prior: { mean: 0.35, std: 0.18, interval_95: [0.1, 0.6] }, posterior: { mean: 0.49, std: 0.028, interval_95: [0.44, 0.54] }, method: "trajectory fit", evidence_ids: ["force-3"], mismatch_notes: ["effective Coulomb model"] }),
  complete("system_identification", 1),
  enter("update_simulation", 1),
  makeEvent("simulation.belief_rebuilt", "update_simulation", { belief_revision: 2, parameters: ["effective_static_friction", "effective_dynamic_friction"], world_instance_id: "belief-world-demo" }),
  frame(6, "update_simulation", 0.292, 0, "belief"),
  complete("update_simulation", 1),
  enter("predict"),
  makeEvent("prediction.completed", "predict", { action_id: "push", scenario_id: "candidate-push", target_error_m: 0.071, success_probability: 0.68, peak_force_n: 4.8, tipping_margin: 0.12, feasible: true, reasons: ["high sliding resistance"] }),
  makeEvent("prediction.completed", "predict", { action_id: "pick_and_place", scenario_id: "candidate-pick", target_error_m: 0.018, success_probability: 0.93, peak_force_n: 3.4, tipping_margin: 0.82, feasible: true, reasons: ["friction-insensitive transport"] }),
  complete("predict"),
  enter("select_action"),
  makeEvent("action.selected", "select_action", { selected_action_id: "pick_and_place", candidate_scores: [{ id: "push", score: 0.54 }, { id: "pick_and_place", score: 0.86 }], rationale_event_id: "demo-event-7" }),
  complete("select_action"),
  enter("execute"),
  makeEvent("action.executed", "execute", { action_id: "pick_and_place", controller_result: "completed", observed_terminal_state: "released_in_target", safety_events: [] }),
  frame(7, "execute", 0.43, 2.1),
  frame(8, "execute", 0.61, 0),
  complete("execute"),
  enter("verify"),
  makeEvent("verification.completed", "verify", { success: true, goal_predicates: { cube_in_target: true, robot_safe: true }, prediction_residual_m: 0.032, tolerance_m: 0.05 }),
  complete("verify"),
  enter("learn"),
  makeEvent("learning.completed", "learn", { affected_parameters: ["mass"], old_uncertainty: 0.04, new_uncertainty: 0.06, residual_evidence: ["verification-1"], follow_up_decision: "retain effective friction estimate; widen mass posterior" }),
  complete("learn"),
  makeEvent("run.completed", null, { state: "completed", reason: "task_verified", selected_action: "pick_and_place", duration_s: 18.4 }),
];

export const demoRunId = runId;
