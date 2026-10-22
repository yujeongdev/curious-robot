"""Allowlisted task definitions and public presets backed by data files."""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType

from pydantic import BaseModel

from curious_robot.objects import get_object_archetype

from .contracts import TaskDefinition, TaskPreset

_CONFIG_DIRECTORY = Path(__file__).parents[1] / "configs" / "tasks"


def _load[Model: BaseModel](filename: str, model: type[Model]) -> Model:
    path = _CONFIG_DIRECTORY / filename
    return model.model_validate(json.loads(path.read_text(encoding="utf-8")))


_CATALOG = json.loads((_CONFIG_DIRECTORY / "catalog.json").read_text(encoding="utf-8"))
if _CATALOG.get("schema_version") != "1.0.0":
    raise RuntimeError("unsupported task catalog schema")
_LOADED_TASKS = tuple(_load(filename, TaskDefinition) for filename in _CATALOG["definitions"])
_LOADED_PRESETS = tuple(_load(filename, TaskPreset) for filename in _CATALOG["presets"])
_TASKS = MappingProxyType(
    {(item.task_definition_id, item.version): item for item in _LOADED_TASKS}
)
_PRESETS = MappingProxyType({item.preset_id: item for item in _LOADED_PRESETS})


def get_task_definition(task_definition_id: str, version: str) -> TaskDefinition:
    try:
        return _TASKS[(task_definition_id, version)]
    except KeyError as error:
        raise KeyError(f"unsupported task definition: {task_definition_id}@{version}") from error


def get_task_preset(preset_id: str) -> TaskPreset:
    """Resolve and cross-check an allowlisted preset without importing code by name."""

    try:
        preset = _PRESETS[preset_id]
    except KeyError as error:
        raise KeyError(f"unsupported task preset: {preset_id}") from error
    definition = get_task_definition(preset.task_definition_id, preset.task_definition_version)
    archetype = get_object_archetype(preset.object_archetype_id, preset.object_archetype_version)
    action_families = {
        "push": "controlled_push",
        "pick_place": "pick_and_place",
    }
    inadmissible = {
        action.action_id
        for action in preset.action_candidates
        if action_families[action.kind] not in definition.permitted_action_families
    }
    if inadmissible:
        raise ValueError(f"task preset contains inadmissible actions: {sorted(inadmissible)}")
    surfaces = {surface.surface_id: surface for surface in archetype.contact_surfaces}
    if preset.experiment_contact_surface_id not in surfaces:
        raise ValueError("task experiment contact surface is absent from its object archetype")
    for action in preset.action_candidates:
        if action.kind != "push":
            continue
        surface = surfaces.get(action.contact_surface_id or "")
        if surface is None:
            raise ValueError(f"action {action.action_id} names an unknown contact surface")
        if abs(action.lateral_contact_offset_m) > surface.half_size_m[0]:
            raise ValueError(f"action {action.action_id} lateral offset lies outside the contact face")
        if action.push_height_m is None or action.push_height_m > surface.half_size_m[1]:
            raise ValueError(f"action {action.action_id} push height lies outside the contact face")
    return preset


def list_task_presets() -> tuple[TaskPreset, ...]:
    return tuple(_PRESETS.values())


PLANAR_TRANSLATION_V1 = get_task_definition("planar-translation", "1.0.0")
PLANAR_POSE_MATCH_V1 = get_task_definition("planar-pose-match", "1.0.0")
PUSH_CUBE_V1 = get_task_preset("push-cube-v1")
PUSH_T_BAR_V1 = get_task_preset("push-t-bar-v1")
