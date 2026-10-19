import pytest

from curious_robot.surface import SurfaceFamily, sample_surface_profile, surface_family_bounds


@pytest.mark.parametrize("family", list(SurfaceFamily))
def test_surface_profiles_are_reproducible_and_bounded(family: SurfaceFamily) -> None:
    first = sample_surface_profile(71, family)
    second = sample_surface_profile(71, family)
    bounds = surface_family_bounds()[family]
    assert first == second
    assert (
        bounds["effective_static_friction"][0]
        <= first.effective_static_friction
        <= bounds["effective_static_friction"][1]
    )
    assert (
        bounds["effective_dynamic_friction"][0]
        <= first.effective_dynamic_friction
        <= min(bounds["effective_dynamic_friction"][1], first.effective_static_friction - 0.03)
    )
    assert bounds["restitution"][0] <= first.restitution <= bounds["restitution"][1]
    assert bounds["visual_roughness"][0] <= first.visual_roughness <= bounds["visual_roughness"][1]


def test_seed_selects_a_family_without_exposing_component_material_coefficients() -> None:
    profile = sample_surface_profile(7)
    assert profile.family in SurfaceFamily
    assert not hasattr(profile, "cube_friction")
    assert not hasattr(profile, "table_friction")
