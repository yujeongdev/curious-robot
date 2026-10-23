"""Resolve one public task binding for CLI, HTTP and simulation consumers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from curious_robot.objects import ObjectArchetype, get_object_archetype

from .contracts import TaskDefinition, TaskPreset
from .registry import get_task_definition, get_task_preset


@dataclass(frozen=True, slots=True)
class ResolvedTask:
    """An immutable binding, with transport-neutral summary and detail views."""

    preset: TaskPreset
    definition: TaskDefinition
    archetype: ObjectArchetype

    def summary(self) -> dict[str, Any]:
        return {
            "task_id": self.preset.preset_id,
            "task_version": self.preset.version,
            "task_preset_hash": self.preset.preset_hash,
            "objective": self.definition.objective,
            "object_label": self.archetype.label,
            "requires_yaw": self.preset.goal.requires_yaw,
            "candidate_count": len(self.preset.action_candidates),
            "default_material_bounds": {"mass_kg": list(self.preset.default_mass_range_kg)},
        }

    def document(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "preset": self.preset.model_dump(mode="json"),
            "definition": self.definition.model_dump(mode="json"),
            "object_archetype": self.archetype.model_dump(mode="json"),
        }


def resolve_task(task_id: str) -> ResolvedTask:
    preset = get_task_preset(task_id)
    return ResolvedTask(
        preset=preset,
        definition=get_task_definition(preset.task_definition_id, preset.task_definition_version),
        archetype=get_object_archetype(preset.object_archetype_id, preset.object_archetype_version),
    )
