"""Versioned public object geometry."""

from .catalog import CUBE_V1, T_BAR_V1, get_object_archetype, list_object_archetypes
from .contracts import ContactSurface, ObjectArchetype, RigidBoxPart

__all__ = [
    "CUBE_V1",
    "T_BAR_V1",
    "ContactSurface",
    "ObjectArchetype",
    "RigidBoxPart",
    "get_object_archetype",
    "list_object_archetypes",
]
