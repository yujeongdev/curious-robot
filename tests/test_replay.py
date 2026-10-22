from curious_robot.replay import resolve_initial_object_state


def test_object_neutral_replay_state_does_not_require_legacy_cube_keys() -> None:
    manifest = {
        "initial_object_position_m": [0.1, 0.0, 0.02],
        "initial_object_body_q": [0.1, 0.0, 0.02, 0.0, 0.0, 0.0, 1.0],
        "initial_object_body_qd": [0.0] * 6,
    }
    position, body_q, body_qd = resolve_initial_object_state(manifest)
    assert position == (0.1, 0.0, 0.02)
    assert len(body_q) == 7
    assert len(body_qd) == 6


def test_legacy_cube_replay_state_remains_readable() -> None:
    manifest = {
        "initial_cube_position_m": [0.1, 0.0, 0.025],
        "initial_cube_body_q": [0.1, 0.0, 0.025, 0.0, 0.0, 0.0, 1.0],
        "initial_cube_body_qd": [0.0] * 6,
    }
    assert resolve_initial_object_state(manifest)[0] == (0.1, 0.0, 0.025)
