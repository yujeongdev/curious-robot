from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from curious_robot.action_graph import NodeOutcome, compile_motion_action_graph
from curious_robot.events import (
    EVENT_ADAPTER,
    STAGE_TO_MACRO,
    ActionGraphCompiledEvent,
    ActionGraphCompiledPayload,
    ActionNodeTransitionedEvent,
    ActionNodeTransitionedPayload,
    BodyTransform,
    DiscoveryStage,
    EventContractError,
    MacroPhase,
    RationalePayload,
    RationalePublishedEvent,
    RunPayload,
    RunStartedEvent,
    SimulationVisualFrameEvent,
    VisualFramePayload,
    parse_event,
    serialize_event,
    validate_public_data,
    validate_public_event,
)


def common(
    sequence: int = 1,
    stage: DiscoveryStage = DiscoveryStage.OBSERVE,
) -> dict[str, object]:
    return {
        "run_id": uuid4(),
        "sequence": sequence,
        "wall_time": datetime.now(UTC),
        "stage": stage,
        "iteration_id": 0,
        "stage_instance_id": uuid4(),
    }


def action_graph():
    return compile_motion_action_graph(
        task_id="push-t-bar-v1",
        task_version="1.0.0",
        object_id="unknown-t-bar",
        object_archetype_version="1.0.0",
        geometry_hash="sha256:" + "a" * 64,
        calibration_id="sha256:" + "b" * 64,
        protocol_binding_id="sha256:" + "c" * 64,
        phases=("contact_approach", "contact_push", "retreat"),
        phase_durations_s=(0.5, 1.0, 0.5),
        contact_expectations=("acquire_at_end", "maintain", "release"),
        observation_timeout_s=1.0,
        wall_clock_deadline_s=10.0,
    )


def test_event_union_round_trips_and_retains_same_major_optional_fields() -> None:
    event = RunStartedEvent(
        **common(),
        payload=RunPayload(state="initializing", configuration_commitment="sha256:opaque"),
        future_optional={"supported": True},
    )

    encoded = serialize_event(event)
    decoded = parse_event(encoded)

    assert decoded.type == "run.started"
    assert decoded.future_optional == {"supported": True}
    assert serialize_event(decoded) == encoded


def test_newer_schema_major_and_unknown_event_type_stop_replay() -> None:
    event = RunStartedEvent(**common(), payload=RunPayload(state="initializing"))
    document = event.model_dump(mode="json")
    document["schema_version"] = "2.0.0"
    with pytest.raises(ValidationError, match="upgrade required"):
        EVENT_ADAPTER.validate_python(document)

    document["schema_version"] = "1.1.0"
    document["type"] = "future.telepathy"
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        EVENT_ADAPTER.validate_python(document)


def test_action_graph_commitment_round_trips_with_content_identity() -> None:
    graph = action_graph()
    event = ActionGraphCompiledEvent(
        **common(stage=DiscoveryStage.SELECT_ACTION),
        payload=ActionGraphCompiledPayload(
            graph_id=graph.graph_id,
            graph=graph,
            motion_plan_id="sha256:" + "d" * 64,
            deployment_bundle_id="sha256:" + "e" * 64,
        ),
    )

    validate_public_event(event)
    decoded = parse_event(serialize_event(event))

    assert decoded.type == "action.graph_compiled"
    assert decoded.payload.graph_id == graph.graph_id
    assert decoded.payload.graph.graph_id == graph.graph_id
    assert serialize_event(decoded) == serialize_event(event)


def test_action_graph_commitment_rejects_mismatched_identity_and_truth_fields() -> None:
    graph = action_graph()
    with pytest.raises(ValidationError, match="does not match"):
        ActionGraphCompiledPayload(graph_id="sha256:" + "f" * 64, graph=graph)
    with pytest.raises(ValidationError, match="requires a motion plan"):
        ActionGraphCompiledPayload(
            graph_id=graph.graph_id,
            graph=graph,
            deployment_bundle_id="sha256:" + "e" * 64,
        )

    graph_document = graph.model_dump(mode="python")
    graph_document["nodes"][0]["parameters"]["hidden_mass_kg"] = 0.42
    leaked_graph = type(graph).model_validate(graph_document)
    event = ActionGraphCompiledEvent(
        **common(stage=DiscoveryStage.SELECT_ACTION),
        payload=ActionGraphCompiledPayload(
            graph_id=leaked_graph.graph_id,
            graph=leaked_graph,
        ),
    )
    with pytest.raises(EventContractError, match="hidden_mass_kg"):
        validate_public_event(event)


def test_action_node_transition_is_observation_not_an_advance_command() -> None:
    graph = action_graph()
    entered = ActionNodeTransitionedEvent(
        **common(stage=DiscoveryStage.EXECUTE),
        payload=ActionNodeTransitionedPayload(
            graph_id=graph.graph_id,
            node_id=graph.entry_node_id,
            state="entered",
        ),
    )
    completed = ActionNodeTransitionedEvent(
        **common(sequence=2, stage=DiscoveryStage.EXECUTE),
        payload=ActionNodeTransitionedPayload(
            graph_id=graph.graph_id,
            node_id=graph.entry_node_id,
            state="completed",
            outcome=NodeOutcome.SUCCESS,
            elapsed_s=0.25,
            evidence_ids=("sha256:" + "9" * 64,),
        ),
    )

    validate_public_event(entered)
    validate_public_event(completed)
    assert completed.payload.outcome is NodeOutcome.SUCCESS
    assert parse_event(serialize_event(completed)).payload.node_id == graph.entry_node_id

    with pytest.raises(ValidationError, match="cannot declare"):
        ActionNodeTransitionedPayload(
            graph_id=graph.graph_id,
            node_id=graph.entry_node_id,
            state="entered",
            outcome=NodeOutcome.SUCCESS,
        )
    with pytest.raises(ValidationError, match="do not match"):
        ActionNodeTransitionedPayload(
            graph_id=graph.graph_id,
            node_id=graph.entry_node_id,
            state="safety_trip",
            outcome=NodeOutcome.FAILURE,
        )


@pytest.mark.parametrize(
    ("state", "outcome"),
    [
        ("failed", NodeOutcome.FAILURE),
        ("safety_trip", NodeOutcome.SAFETY_TRIP),
        ("goal_miss", NodeOutcome.GOAL_MISS),
    ],
)
def test_action_node_terminal_outcomes_remain_distinct(
    state: str,
    outcome: NodeOutcome,
) -> None:
    graph = action_graph()
    transition = ActionNodeTransitionedPayload(
        graph_id=graph.graph_id,
        node_id="verify_goal",
        state=state,
        outcome=outcome,
    )
    assert transition.state == state
    assert transition.outcome is outcome

def test_public_validation_blocks_truth_fields_sentinels_and_nonfinite_values() -> None:
    with pytest.raises(EventContractError, match="hidden_mass_kg"):
        validate_public_data({"result": {"hidden_mass_kg": 0.42}})
    with pytest.raises(EventContractError, match="sentinel"):
        validate_public_data({"ordinary_looking_value": 812.125}, forbidden_numeric_values=(812.125,))
    with pytest.raises(EventContractError, match="non-finite"):
        validate_public_data({"force": float("nan")})


def test_rationale_is_structured_evidence_not_private_reasoning() -> None:
    event = RationalePublishedEvent(
        **common(),
        payload=RationalePayload(
            summary="Static friction uncertainty dominates push outcome.",
            evidence_ids=("prior:mu-static",),
            considered_option_ids=("weigh", "probe-static"),
            selected_option_id="probe-static",
        ),
    )
    validate_public_event(event)
    assert event.payload.selected_option_id == "probe-static"


def test_visual_frame_requires_stable_unique_body_and_asset_ids() -> None:
    body = BodyTransform(
        body_id="cube",
        label="unknown cube",
        asset_node_id="cube-node",
        position_m=(0.4, 0.0, 0.03),
        orientation_xyzw=(0.0, 0.0, 0.0, 1.0),
    )
    payload = {
        "frame_id": 4,
        "source_state_sequence": 3,
        "scene": "truth",
        "world_instance_id": uuid4(),
        "body_manifest_id": "truth-v1",
        "sim_time_s": 0.5,
        "presentation_time_s": 0.5,
        "bodies": (body, body),
    }
    with pytest.raises(ValidationError, match="body_id values must be unique"):
        VisualFramePayload.model_validate(payload)

    payload["bodies"] = (body,)
    payload["object_geometry"] = {
        "archetype_id": "t-bar",
        "archetype_version": "1.0.0",
        "geometry_hash": "sha256:" + "a" * 64,
        "body_id": "cube",
        "parts": [
            {
                "part_id": "stem",
                "center_object_m": (-0.01, 0.0, 0.0),
                "half_extents_m": (0.04, 0.01, 0.02),
            }
        ],
    }
    payload["target_region"] = {
        "center_m": (0.15, 0.0, 0.001),
        "radius_m": 0.03,
        "yaw_rad": 0.0,
        "yaw_tolerance_rad": 0.15707963267948966,
        "geometry": {
            key: value
            for key, value in payload["object_geometry"].items()
            if key != "body_id"
        },
        "label": "align t-bar silhouette",
    }
    frame = SimulationVisualFrameEvent(
        **common(), frame_id=4, sim_time_s=0.5, payload=VisualFramePayload.model_validate(payload)
    )
    assert frame.payload.scene == "truth"
    assert frame.payload.object_geometry.archetype_id == "t-bar"
    assert frame.payload.target_region is not None
    assert frame.payload.target_region.center_m[2] == 0.001
    assert frame.payload.target_region.geometry is not None
    assert (
        frame.payload.target_region.geometry.geometry_hash
        == frame.payload.object_geometry.geometry_hash
    )
    assert len(frame.payload.target_region.geometry.parts) == 1
    invalid_geometry = {**payload, "object_geometry": {**payload["object_geometry"], "body_id": "missing"}}
    with pytest.raises(ValidationError, match="must reference a body"):
        VisualFramePayload.model_validate(invalid_geometry)
    leaked_target = frame.payload.model_dump()
    leaked_target["target_region"]["geometry"]["hidden_mass_kg"] = 0.4
    with pytest.raises(EventContractError, match="hidden_mass_kg"):
        validate_public_data(leaked_target)
    mismatched_target = frame.payload.model_dump()
    mismatched_target["target_region"]["geometry"]["geometry_hash"] = "sha256:" + "b" * 64
    with pytest.raises(ValidationError, match="must exactly match"):
        VisualFramePayload.model_validate(mismatched_target)
    assert frame.frame_id == frame.payload.frame_id

    mismatched = frame.model_copy(update={"frame_id": 5})
    with pytest.raises(EventContractError, match="frame_id must match"):
        validate_public_event(mismatched)


def test_twelve_stages_map_to_six_macro_phases() -> None:
    assert set(STAGE_TO_MACRO) == set(DiscoveryStage)
    assert set(STAGE_TO_MACRO.values()) == set(MacroPhase)
    assert STAGE_TO_MACRO[DiscoveryStage.SYSTEM_IDENTIFICATION] == MacroPhase.CALIBRATE
    assert STAGE_TO_MACRO[DiscoveryStage.SELECT_ACTION] == MacroPhase.DECIDE


def test_serialization_is_compact_stable_utf8_json() -> None:
    event = RunStartedEvent(**common(), payload=RunPayload(state="초기화"))
    encoded = serialize_event(event)
    assert b"\n" not in encoded
    assert b" " not in encoded
    assert json.loads(encoded)["payload"]["state"] == "초기화"
