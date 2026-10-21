import type { AgentEvent, DetailedStage } from "../generated/agent-events";

// Hand-authored UI illustration: these events, values, and poses are not an executed run.
const runId = "synthetic-preview-agent-lab";
export const demoPreviewNotice = "Synthetic preview: hand-authored events, values, and robot poses illustrate the interface. No Newton episode was executed for this preview.";
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

function frame(frameId: number, stage: DetailedStage, cubeX: number, forceN = 0, scene: "truth" | "belief" = "truth", iteration = 0) {
  return makeEvent("simulation.visual_frame", stage, {
    frame_id: frameId,
    source_state_sequence: sequence,
    scene,
    world_instance_id: `${scene}-world-demo`,
    belief_revision: scene === "belief" ? 3 : null,
    body_manifest_id: scene === "truth" ? "franka-cube-v1" : "cube-rollout-v1",
    sim_time_s: frameId * 0.2,
    presentation_time_s: frameId * 0.2,
    bodies: robotBodies(cubeX, Math.min(0.12, forceN * 0.012)),
    contacts: forceN > 0 ? [{ position_m: [cubeX - 0.055, 0, 0.055], normal_world: [-1, 0, 0], force_world_n: [forceN, 0, 0], shape_a_label: "panda_hand", shape_b_label: "cube" }] : [],
    vectors: forceN > 0 ? [{ vector_id: `force-${frameId}`, kind: "measured_force", origin_m: [cubeX - 0.055, 0, 0.07], vector: [forceN, 0, 0], frame: "world", units: "N", label: "measured contact force" }] : [],
    target_region: { center_m: [0.62, 0, 0.005], radius_m: 0.09 },
    camera_hint: { preset: forceN > 0 ? "experiment" : "overview", target_m: [cubeX, 0, 0.05] },
  }, { sim: frameId * 0.2, frame: frameId, iteration });
}

function enter(stage: DetailedStage, iteration = 0, detail?: string) {
  return makeEvent("stage.entered", stage, { detail: detail ?? stage.replaceAll("_", " "), iteration }, { iteration });
}

function complete(stage: DetailedStage, iteration = 0, outcome = "completed") {
  return makeEvent("stage.completed", stage, { outcome, duration_s: 1.8, evidence_ids: [] }, { iteration });
}

export const demoEvents: AgentEvent[] = [
  makeEvent("run.started", null, { state: "preparing", preset: "synthetic-ui-preview", data_provenance: "synthetic_illustration", reason: demoPreviewNotice }, { sim: 0 }),
  enter("observe"),
  makeEvent("rationale.published", "observe", { summary: "Geometry is known. Physical pusher experiments can constrain effective cube–surface friction; mass remains a prior because bilateral grasp and lift are unvalidated.", evidence_ids: [], considered_option_ids: [], selected_option_id: "inspect-scene" }),
  frame(1, "observe", 0.28),
  complete("observe"),
  enter("reason"),
  makeEvent("rationale.published", "reason", { summary: "Probe breakaway with the Franka-mounted pusher, then measure sliding resistance with maintained physical contact. Neither experiment validates a grasp or identifies mass independently.", evidence_ids: [], considered_option_ids: ["static_breakaway_ramp", "dynamic_controlled_slide"], selected_option_id: "static_breakaway_ramp" }),
  complete("reason"),
  enter("identify_unknowns"),
  makeEvent("belief.updated", "identify_unknowns", { revision: 1, parameter_name: "effective_static_friction", unit: "μ", prior: { mean: 0.55, std: 0.20, interval_95: [0.2, 0.9] }, posterior: { mean: 0.55, std: 0.20, interval_95: [0.2, 0.9] }, method: "bounded prior", evidence_ids: [], mismatch_notes: [] }),
  makeEvent("belief.updated", "identify_unknowns", { revision: 1, parameter_name: "mass_kg", unit: "kg", prior: { mean: 0.265, std: 0.107, interval_95: [0.08, 0.45] }, posterior: { mean: 0.265, std: 0.107, interval_95: [0.08, 0.45], status: "prior" }, method: "bounded uniform prior; mass remains unidentified", evidence_ids: [], mismatch_notes: ["No validated bilateral grasp/lift measurement"] }),
  complete("identify_unknowns"),
  enter("design_experiment", 1),
  makeEvent("rationale.published", "design_experiment", { summary: "A bounded Franka IK joint-target ramp measures native pusher–cube contact force and camera displacement without applying force directly to the cube.", evidence_ids: [], considered_option_ids: ["static_breakaway_ramp", "dynamic_controlled_slide"], selected_option_id: "static_breakaway_ramp" }, { iteration: 1 }),
  makeEvent("tool.started", "design_experiment", { tool_call_id: "tool-probe-1", tool_name: "probe_static_friction", arguments: { controller: "Franka IK joint-target contact ramp", displacement_threshold_m: 0.003 } }, { iteration: 1 }),
  complete("design_experiment", 1),
  enter("act", 1),
  frame(2, "act", 0.28, 0.8, "truth", 1),
  complete("act", 1),
  enter("measure", 1, "Measure static friction"),
  makeEvent("measurement.sampled", "measure", { evidence_id: "force-1", sensor_kind: "native_contact_force_and_camera_pose", producer: "Newton SensorContact hand/fingers/pusher ↔ cube", frame: "world", units: "SI", values: { measured_force_n: 0.8, object_position_m: 0.28, object_velocity_mps: 0 }, validity: "valid" }, { iteration: 1, sim: 2.0 }),
  frame(3, "measure", 0.28, 0.8, "truth", 1),
  makeEvent("measurement.sampled", "measure", { evidence_id: "force-2", sensor_kind: "native_contact_force_and_camera_pose", producer: "Newton SensorContact hand/fingers/pusher ↔ cube", frame: "world", units: "SI", values: { measured_force_n: 0.998, object_position_m: 0.2823, object_velocity_mps: 0.006 }, validity: "valid" }, { iteration: 1, sim: 2.4 }),
  frame(4, "measure", 0.2823, 0.998, "truth", 1),
  makeEvent("measurement.summary", "measure", { evidence_id: "breakaway-summary", accepted_count: 898, rejected_count: 0, aggregates: { peak_measured_force_n: 0.998, displacement_m: 0.0023 }, quality_flags: ["interval_censored", "mass_remains_prior"] }, { iteration: 1 }),
  makeEvent("tool.completed", "measure", { tool_call_id: "tool-probe-1", tool_name: "probe_static_friction", duration_s: 2.8, result: { peak_measured_force_n: 0.998, displacement_m: 0.0023, evidence_id: "breakaway-summary" } }, { iteration: 1 }),
  complete("measure", 1),
  enter("system_identification", 1),
  makeEvent("belief.updated", "system_identification", { revision: 2, parameter_name: "effective_static_friction", unit: "μ", prior: { mean: 0.55, std: 0.20, interval_95: [0.2, 0.9] }, posterior: { mean: 0.55, std: 0.2021, interval_95: [0.2, 0.9], status: "bounded" }, method: "censored breakaway interval", evidence_ids: ["breakaway-summary"], mismatch_notes: ["Normal load derives from the broad mass prior; friction is not precisely identified"] }, { iteration: 1 }),
  complete("system_identification", 1),
  enter("design_experiment", 2, "Design maintained-contact sliding experiment"),
  makeEvent("rationale.published", "design_experiment", { summary: "The static probe leaves broad friction bounds. A second physical push maintains pusher contact to measure sliding force and motion; mass remains the same prior.", evidence_ids: ["breakaway-summary"], considered_option_ids: ["dynamic_controlled_slide"], selected_option_id: "dynamic_controlled_slide" }, { iteration: 2 }),
  makeEvent("tool.started", "design_experiment", { tool_call_id: "tool-probe-2", tool_name: "probe_dynamic_friction", arguments: { controller: "Franka IK joint-target maintained-contact slide" } }, { iteration: 2 }),
  complete("design_experiment", 2),
  enter("act", 2),
  complete("act", 2),
  enter("measure", 2, "Measure dynamic friction"),
  makeEvent("measurement.sampled", "measure", { evidence_id: "sliding-sample", sensor_kind: "native_contact_force_and_camera_pose", producer: "Newton SensorContact hand/fingers/pusher ↔ cube", frame: "world", units: "SI", values: { measured_force_n: 1.114, object_position_m: 0.3997, object_velocity_mps: 0.04 }, validity: "valid" }, { iteration: 2, sim: 5.0 }),
  frame(5, "measure", 0.3997, 1.114, "truth", 2),
  makeEvent("tool.completed", "measure", { tool_call_id: "tool-probe-2", tool_name: "probe_dynamic_friction", duration_s: 11.05, result: { evidence_id: "sliding-sample", sample_count: 2652, peak_measured_force_n: 1.114, displacement_m: 0.1174 } }, { iteration: 2 }),
  complete("measure", 2),
  enter("system_identification", 2),
  makeEvent("belief.updated", "system_identification", { revision: 3, parameter_name: "effective_dynamic_friction", unit: "μ", prior: { mean: 0.35, std: 0.18, interval_95: [0.1, 0.6] }, posterior: { mean: 0.3249, std: 0.1312, interval_95: [0.1, 0.55], status: "bounded" }, method: "bounded robust least squares: F = m a + mu N sign(v); mass-prior-derived normal load", evidence_ids: ["sliding-sample"], mismatch_notes: ["Coulomb residual is structured or larger than the validation threshold", "Mass remains unidentified"] }, { iteration: 2 }),
  complete("system_identification", 2),
  enter("update_simulation", 2),
  makeEvent("simulation.belief_rebuilt", "update_simulation", { belief_revision: 3, parameters: ["effective_static_friction", "effective_dynamic_friction"], retained_prior_parameters: ["mass_kg"], world_instance_id: "belief-world-demo" }, { iteration: 2 }),
  frame(6, "update_simulation", 0.3997, 0, "belief", 2),
  complete("update_simulation", 2),
  enter("predict"),
  makeEvent("prediction.completed", "predict", { action_id: "push_straight_low", scenario_id: "candidate-push", target_error_m: 0.0186, success_probability: 0.8, peak_force_n: 1.114, tipping_margin: 0.12, feasible: true, reasons: ["physical controlled push with independently constructed belief-world rollout"] }),
  makeEvent("prediction.completed", "predict", { action_id: "pick_and_place", scenario_id: "candidate-pick", success_probability: 0, feasible: false, reasons: ["Bilateral grasp/lift has not passed physical validation"], reason: "Infeasible: bilateral grasp/lift is unvalidated" }),
  complete("predict"),
  enter("select_action"),
  makeEvent("rationale.published", "select_action", { summary: "Select the physical controlled push. Pick-and-place is infeasible because bilateral grasp/lift is unvalidated; it cannot compete as a successful transport option.", evidence_ids: ["breakaway-summary", "sliding-sample"], considered_option_ids: ["push_straight_low", "pick_and_place"], selected_option_id: "push_straight_low" }),
  makeEvent("action.selected", "select_action", { selected_action_id: "push_straight_low", candidate_scores: [{ id: "push_straight_low", score: 0.54, feasible: true }, { id: "pick_and_place", feasible: false, reason: "unvalidated bilateral grasp/lift" }], rationale_event_id: `demo-event-${sequence}` }),
  complete("select_action"),
  enter("execute"),
  makeEvent("action.executed", "execute", { action_id: "push_straight_low", controller_result: "completed", observed_terminal_state: "pushed_into_target", safety_events: [] }),
  frame(7, "execute", 0.43, 1.114),
  frame(8, "execute", 0.61, 0),
  complete("execute"),
  enter("verify"),
  makeEvent("verification.completed", "verify", { success: true, goal_predicates: { cube_in_target: true, robot_safe: true }, prediction_residual_m: 0.0186, tolerance_m: 0.035 }),
  complete("verify"),
  enter("learn"),
  makeEvent("learning.completed", "learn", { affected_parameters: [], retained_prior_parameters: ["mass_kg"], residual_evidence: [], follow_up_decision: "Physical push verified. Retain bounded effective friction estimates and the unchanged mass prior; do not infer mass or grasp capability from task success." }),
  complete("learn"),
  makeEvent("run.completed", null, { state: "completed", reason: "physical_push_verified_mass_remains_prior", selected_action: "push_straight_low", duration_s: 18.4 }),
];

export const demoRunId = runId;
