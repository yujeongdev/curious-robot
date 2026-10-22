import math

import pytest
from pydantic import ValidationError

from curious_robot.belief import initial_belief_for_object
from curious_robot.objects import CUBE_V1, T_BAR_V1, get_object_archetype
from curious_robot.schemas import ParameterName
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
    assert {part.part_id for part in T_BAR_V1.parts} == {"stem", "crossbar"}
    assert T_BAR_V1.geometry_volume_m3 == pytest.approx(0.000175)
    assert T_BAR_V1.parts[0].center_object_m[0] + T_BAR_V1.parts[0].half_extents_m[0] == pytest.approx(
        T_BAR_V1.parts[1].center_object_m[0] - T_BAR_V1.parts[1].half_extents_m[0]
    )
    assert PUSH_T_BAR_V1.experiment_protocol_overrides.dynamic_probe.maximum_travel_m == 0.07
    assert PUSH_CUBE_V1.experiment_protocol_overrides.dynamic_probe.maximum_travel_m is None


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


def test_task_preset_rejects_nonfinite_randomization_bounds() -> None:
    payload = PUSH_T_BAR_V1.model_dump()
    payload["initial_position_jitter_m"] = (
        float("nan"),
        *payload["initial_position_jitter_m"][1:],
    )
    with pytest.raises(ValidationError, match="must be finite"):
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
