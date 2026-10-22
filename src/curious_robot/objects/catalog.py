"""Allowlisted public object catalog backed by validated data files."""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType

from .contracts import ObjectArchetype

_CONFIG_DIRECTORY = Path(__file__).parents[1] / "configs" / "objects"


def _load(filename: str) -> ObjectArchetype:
    path = _CONFIG_DIRECTORY / filename
    return ObjectArchetype.model_validate(json.loads(path.read_text(encoding="utf-8")))


_CATALOG = json.loads((_CONFIG_DIRECTORY / "catalog.json").read_text(encoding="utf-8"))
if _CATALOG.get("schema_version") != "1.0.0":
    raise RuntimeError("unsupported object catalog schema")
_LOADED_OBJECTS = tuple(_load(filename) for filename in _CATALOG["objects"])
_OBJECTS = MappingProxyType(
    {(item.archetype_id, item.version): item for item in _LOADED_OBJECTS}
)


def get_object_archetype(archetype_id: str, version: str) -> ObjectArchetype:
    """Resolve an allowlisted archetype or fail without dynamic imports."""

    try:
        return _OBJECTS[(archetype_id, version)]
    except KeyError as error:
        raise KeyError(f"unsupported object archetype: {archetype_id}@{version}") from error


def list_object_archetypes() -> tuple[ObjectArchetype, ...]:
    return tuple(_OBJECTS.values())


CUBE_V1 = get_object_archetype("cube", "1.0.0")
T_BAR_V1 = get_object_archetype("t-bar", "1.0.0")
