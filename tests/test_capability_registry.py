import json
from pathlib import Path


def test_capability_registry_is_machine_readable_and_has_required_fields() -> None:
    registry = json.loads(Path("docs/capabilities.json").read_text())
    assert registry["runtime"]["newton"] == "1.7.0"
    required = {"name", "reference", "entry_point", "status", "limitations", "chosen_implementation"}
    assert registry["capabilities"]
    assert all(required <= capability.keys() for capability in registry["capabilities"])
