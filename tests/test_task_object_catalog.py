import math
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from curious_robot.belief import initial_belief_for_object
from curious_robot.control import (
    NewtonFrankaController,
    PushExecutionOutcome,
    RefinementAction,
    refinement_goal,
    requested_planar_goal,
    select_refinement_action,
    terminal_push_outcome,
)
from curious_robot.objects import CUBE_V1, T_BAR_V1, get_object_archetype
from curious_robot.protocol import load_default_protocol
from curious_robot.schemas import ParameterName, Pose
from curious_robot.tasks import (
    PUSH_CUBE_V1,
    PUSH_T_BAR_V1,
    PlanarPose,
    evaluate_planar_goal,
    get_task_definition,
    get_task_preset,
)


def test_catalog_preserves_cube_and_adds_non_overlapping_compound_t_bar() -> None:
    assert get_object_archetype("cube", "1.0.0") is CUBE_V1
    assert get_task_preset("push-cube-v1") is PUSH_CUBE_V1
    assert get_task_preset("push-t-bar-v1") is PUSH_T_BAR_V1
    assert PUSH_T_BAR_V1.version == "1.2.0"
    assert {part.part_id for part in T_BAR_V1.parts} == {"stem", "crossbar"}
    assert T_BAR_V1.geometry_volume_m3 == pytest.approx(0.000175)
    assert T_BAR_V1.parts[0].center_object_m[0] + T_BAR_V1.parts[0].half_extents_m[0] == pytest.approx(
        T_BAR_V1.parts[1].center_object_m[0] - T_BAR_V1.parts[1].half_extents_m[0]
    )
    assert PUSH_T_BAR_V1.experiment_protocol_overrides.dynamic_probe.maximum_travel_m == 0.07
    assert PUSH_T_BAR_V1.experiment_protocol_overrides.final_push.maximum_attempts == 3
    assert PUSH_T_BAR_V1.initial_yaw_jitter_rad == pytest.approx(
        (-math.radians(8.0), -math.radians(5.0))
    )
    assert PUSH_CUBE_V1.experiment_protocol_overrides.dynamic_probe.maximum_travel_m is None
    assert PUSH_T_BAR_V1.yaw_alignment.adaptive_translation_surface is True
    assert PUSH_T_BAR_V1.goal.position_tolerance_m == pytest.approx(0.03)
    assert PUSH_T_BAR_V1.goal.yaw_tolerance_rad == pytest.approx(math.radians(9.0))
    assert PUSH_T_BAR_V1.yaw_alignment.stop_tolerance_fraction == pytest.approx(0.5)
    assert PUSH_T_BAR_V1.yaw_alignment.position_stop_tolerance_fraction == pytest.approx(0.5)


def test_public_archetypes_reject_hidden_physical_parameters() -> None:
    payload = CUBE_V1.model_dump()
    payload["mass_kg"] = 0.4
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        type(CUBE_V1).model_validate(payload)


def test_public_archetype_rejects_overlapping_compound_parts() -> None:
    payload = T_BAR_V1.model_dump()
    payload["parts"][1]["center_object_m"] = payload["parts"][0]["center_object_m"]
    with pytest.raises(ValidationError, match="rigid parts overlap"):
        type(T_BAR_V1).model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("center_object_m", (-0.04, 0.0, 0.0), "must lie on"),
        ("outward_normal_object", (1.0, 0.0, 0.0), "must lie on"),
        ("half_size_m", (0.02, 0.02), "bounds exceed"),
    ],
)
def test_public_archetype_rejects_nonphysical_contact_surface(
    field: str,
    value: tuple[float, ...],
    message: str,
) -> None:
    payload = T_BAR_V1.model_dump()
    payload["contact_surfaces"][0][field] = value
    with pytest.raises(ValidationError, match=message):
        type(T_BAR_V1).model_validate(payload)


def test_t_bar_contact_operating_point_is_bounded_by_stem_face() -> None:
    surface = T_BAR_V1.validate_contact_operating_point(
        surface_id="stem_rear_x",
        lateral_offset_m=0.012,
        push_height_object_m=0.012,
    )
    assert surface.surface_id == "stem_rear_x"
    with pytest.raises(ValueError, match="lateral contact offset"):
        T_BAR_V1.validate_contact_operating_point(
            surface_id="stem_rear_x",
            lateral_offset_m=0.013,
            push_height_object_m=0.012,
        )
    with pytest.raises(ValueError, match="push height"):
        T_BAR_V1.validate_contact_operating_point(
            surface_id="stem_rear_x",
            lateral_offset_m=0.0,
            push_height_object_m=0.021,
        )


def test_t_bar_translation_recovery_selects_an_opposing_public_face() -> None:
    task_config = SimpleNamespace(task_id="push-t-bar-v1")

    class FakeWorld:
        config = task_config

        @staticmethod
        def validate_contact_operating_point(
            *, surface_id: str, lateral_offset_m: float, push_height_object_m: float
        ):
            return T_BAR_V1.validate_contact_operating_point(
                surface_id=surface_id,
                lateral_offset_m=lateral_offset_m,
                push_height_object_m=push_height_object_m,
            )

    controller = NewtonFrankaController(FakeWorld())  # type: ignore[arg-type]
    protocol = load_default_protocol().final_push.model_copy(
        update={"contact_surface_id": "stem_rear_x", "lateral_ik_compensation_m": 0.008}
    )
    pose = Pose(position_m=(0.0, 0.0, 0.02))
    forward = controller._translation_contact_surface(
        pose,
        push_direction=np.asarray((1.0, 0.0)),
        protocol=protocol,
    )
    lateral = controller._translation_contact_surface(
        pose,
        push_direction=np.asarray((0.0, 1.0)),
        protocol=protocol,
    )
    assert forward.surface_id == "stem_rear_x"
    assert lateral.surface_id == "crossbar_right_y"


@pytest.mark.parametrize(
    ("prior", "goal_success", "expected"),
    [
        (PushExecutionOutcome.COMPLETED, False, PushExecutionOutcome.TRAVEL_EXHAUSTED),
        (PushExecutionOutcome.TRAVEL_EXHAUSTED, True, PushExecutionOutcome.COMPLETED),
        (PushExecutionOutcome.SAFETY_STOPPED, True, PushExecutionOutcome.SAFETY_STOPPED),
    ],
)
def test_terminal_push_outcome_uses_final_settled_pose_unless_safety_stopped(
    prior: PushExecutionOutcome,
    goal_success: bool,
    expected: PushExecutionOutcome,
) -> None:
    assert terminal_push_outcome(prior, goal_success=goal_success) is expected


def test_requested_push_goal_does_not_fall_back_to_catalog_target() -> None:
    goal = requested_planar_goal(
        (0.31, -0.07, 0.02),
        position_tolerance_m=0.012,
        target_yaw_rad=0.4,
        yaw_tolerance_rad=0.05,
    )

    assert goal.target == PlanarPose(x_m=0.31, y_m=-0.07, yaw_rad=0.4)
    assert goal.position_tolerance_m == pytest.approx(0.012)
    assert goal.yaw_tolerance_rad == pytest.approx(0.05)


def test_t_bar_controller_target_is_stricter_than_public_acceptance() -> None:
    refined = refinement_goal(PUSH_T_BAR_V1.goal, PUSH_T_BAR_V1.yaw_alignment)

    assert refined.position_tolerance_m == pytest.approx(0.015)
    assert refined.yaw_tolerance_rad == pytest.approx(math.radians(4.5))
    pose = PlanarPose(x_m=0.166, y_m=0.0, yaw_rad=math.radians(4.0))
    assert evaluate_planar_goal(PUSH_T_BAR_V1.goal, pose).success is True
    assert evaluate_planar_goal(refined, pose).success is False


def test_controller_skips_translation_when_only_public_yaw_remains() -> None:
    yaw_only = evaluate_planar_goal(
        PUSH_T_BAR_V1.goal,
        PlanarPose(x_m=0.155, y_m=0.0, yaw_rad=math.radians(10.0)),
    )
    translation = evaluate_planar_goal(
        PUSH_T_BAR_V1.goal,
        PlanarPose(x_m=0.19, y_m=0.0, yaw_rad=0.0),
    )

    assert select_refinement_action(yaw_only) is RefinementAction.YAW_ONLY
    assert select_refinement_action(translation) is RefinementAction.TRANSLATE


def test_disabled_yaw_policy_preserves_cube_half_radius_settling_margin() -> None:
    refined = refinement_goal(PUSH_CUBE_V1.goal, PUSH_CUBE_V1.yaw_alignment)

    assert refined.position_tolerance_m == pytest.approx(
        PUSH_CUBE_V1.goal.position_tolerance_m * 0.5
    )
    assert refined.yaw_tolerance_rad is None


def test_push_already_inside_refinement_goal_does_not_apply_yaw_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    yaw_rad = math.radians(2.0)

    class AlreadyAtGoalWorld:
        config = SimpleNamespace(
            task_id="push-t-bar-v1",
            include_robot=True,
            maximum_tool_force_n=15.0,
            target_radius_m=0.03,
            target_yaw_rad=0.0,
            target_yaw_tolerance_rad=math.radians(9.0),
        )

        @staticmethod
        def observe_object_pose():  # noqa: ANN205
            return SimpleNamespace(
                pose=Pose(
                    position_m=(0.15, 0.0, 0.02),
                    orientation_xyzw=(0.0, 0.0, math.sin(yaw_rad / 2.0), math.cos(yaw_rad / 2.0)),
                )
            )

    controller = NewtonFrankaController(AlreadyAtGoalWorld())  # type: ignore[arg-type]

    def fail_if_yaw_alignment_runs(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("yaw alignment must not run after the refinement goal passes")

    monkeypatch.setattr(controller, "_align_task_yaw", fail_if_yaw_alignment_runs)
    result = controller.execute_push(
        (0.15, 0.0, 0.02),
        max_force_n=5.0,
        protocol=load_default_protocol().final_push,
    )

    assert result.outcome is PushExecutionOutcome.COMPLETED
    assert result.samples == ()
    assert result.contact_surface_ids == ()


def test_task_preset_rejects_nonfinite_randomization_bounds() -> None:
    payload = PUSH_T_BAR_V1.model_dump()
    payload["initial_position_jitter_m"] = (
        float("nan"),
        *payload["initial_position_jitter_m"][1:],
    )
    with pytest.raises(ValidationError, match="must be finite"):
        type(PUSH_T_BAR_V1).model_validate(payload)


def test_task_preset_rejects_unsafe_initial_yaw_randomization() -> None:
    payload = PUSH_T_BAR_V1.model_dump()
    payload["initial_yaw_jitter_rad"] = (-math.pi / 2.0, math.pi / 2.0)
    with pytest.raises(ValidationError, match="initial-yaw jitter"):
        type(PUSH_T_BAR_V1).model_validate(payload)


def test_t_bar_pose_goal_rejects_correct_position_with_wrong_yaw() -> None:
    definition = get_task_definition(
        PUSH_T_BAR_V1.task_definition_id,
        PUSH_T_BAR_V1.task_definition_version,
    )
    assert "same-shape target silhouette" in definition.objective
    target = PUSH_T_BAR_V1.goal.target
    wrong_yaw = PlanarPose(x_m=target.x_m, y_m=target.y_m, yaw_rad=target.yaw_rad + math.radians(12.0))
    result = evaluate_planar_goal(PUSH_T_BAR_V1.goal, wrong_yaw)
    assert result.predicates == {
        "position_within_tolerance": True,
        "yaw_within_tolerance": False,
    }
    assert result.success is False


def test_t_bar_com_is_an_explicit_public_geometry_assumption() -> None:
    belief = initial_belief_for_object("t-bar", "1.0.0")
    centroid = T_BAR_V1.uniform_density_centroid_m
    assert centroid[0] == pytest.approx(0.01607142857142857)
    assert belief.parameters[ParameterName.COM_X].mean == pytest.approx(centroid[0])
    assert belief.parameters[ParameterName.COM_X].std == 0.0
    assert belief.parameters[ParameterName.COM_X].evidence_ids == ()
    assert "uniform-density COM fixed from public geometry" in belief.assumptions


def test_cube_goal_is_translation_only() -> None:
    target = PUSH_CUBE_V1.goal.target
    result = evaluate_planar_goal(
        PUSH_CUBE_V1.goal,
        PlanarPose(x_m=target.x_m, y_m=target.y_m, yaw_rad=math.pi),
    )
    assert result.success is True
    assert result.yaw_error_rad is None


@pytest.mark.parametrize(
    ("resolver", "args"),
    [
        (get_object_archetype, ("unknown", "1.0.0")),
        (get_task_preset, ("unknown-v1",)),
    ],
)
def test_catalogs_fail_closed_for_unknown_ids(resolver: object, args: tuple[str, ...]) -> None:
    with pytest.raises(KeyError, match="unsupported"):
        resolver(*args)  # type: ignore[operator]
