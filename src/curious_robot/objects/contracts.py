"""Public, versioned object geometry contracts.

These models deliberately cannot represent mass, friction, inertia, or center of
mass. Those values belong to a Reality-private scenario or an evidence-backed
Twin posterior, never to an object archetype.
"""

from __future__ import annotations

import hashlib
import math

from pydantic import Field, model_validator

from curious_robot.schemas import FrozenModel, Quat, Vec3

Vec2 = tuple[float, float]


class RigidBoxPart(FrozenModel):
    """One non-overlapping box in an object-local compound rigid body."""

    part_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    center_object_m: Vec3
    half_extents_m: Vec3
    orientation_object_xyzw: Quat = (0.0, 0.0, 0.0, 1.0)

    @model_validator(mode="after")
    def validate_geometry(self) -> RigidBoxPart:
        values = (*self.center_object_m, *self.half_extents_m, *self.orientation_object_xyzw)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("rigid-part geometry must be finite")
        if any(value <= 0.0 for value in self.half_extents_m):
            raise ValueError("rigid-part half extents must be positive")
        norm = math.sqrt(sum(value * value for value in self.orientation_object_xyzw))
        if not math.isclose(norm, 1.0, abs_tol=1.0e-6):
            raise ValueError("rigid-part orientation must be a unit quaternion")
        return self

    @property
    def volume_m3(self) -> float:
        return 8.0 * math.prod(self.half_extents_m)


class ContactSurface(FrozenModel):
    """Public object-local planar face usable by a contact planner."""

    surface_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    part_id: str = Field(min_length=1)
    center_object_m: Vec3
    outward_normal_object: Vec3
    tangent_object: Vec3
    half_size_m: tuple[float, float]

    @model_validator(mode="after")
    def validate_frame(self) -> ContactSurface:
        values = (
            *self.center_object_m,
            *self.outward_normal_object,
            *self.tangent_object,
            *self.half_size_m,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("contact-surface geometry must be finite")
        if any(value <= 0.0 for value in self.half_size_m):
            raise ValueError("contact-surface half sizes must be positive")
        normal_norm = math.sqrt(sum(value * value for value in self.outward_normal_object))
        tangent_norm = math.sqrt(sum(value * value for value in self.tangent_object))
        dot = sum(
            normal * tangent
            for normal, tangent in zip(self.outward_normal_object, self.tangent_object, strict=True)
        )
        if not math.isclose(normal_norm, 1.0, abs_tol=1.0e-6):
            raise ValueError("contact-surface normal must be a unit vector")
        if not math.isclose(tangent_norm, 1.0, abs_tol=1.0e-6):
            raise ValueError("contact-surface tangent must be a unit vector")
        if not math.isclose(dot, 0.0, abs_tol=1.0e-6):
            raise ValueError("contact-surface normal and tangent must be orthogonal")
        return self


class ObjectArchetype(FrozenModel):
    """Known geometry and affordances for one rigid object family."""

    archetype_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    version: str = Field(pattern=r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$")
    label: str = Field(min_length=1)
    reference_frame: str = Field(min_length=1)
    parts: tuple[RigidBoxPart, ...] = Field(min_length=1)
    support_polygon_xy_m: tuple[Vec2, ...] = Field(min_length=3)
    contact_surfaces: tuple[ContactSurface, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_topology(self) -> ObjectArchetype:
        part_ids = tuple(part.part_id for part in self.parts)
        surface_ids = tuple(surface.surface_id for surface in self.contact_surfaces)
        if len(set(part_ids)) != len(part_ids):
            raise ValueError("object part IDs must be unique")
        if len(set(surface_ids)) != len(surface_ids):
            raise ValueError("contact-surface IDs must be unique")
        if any(surface.part_id not in part_ids for surface in self.contact_surfaces):
            raise ValueError("every contact surface must reference a declared rigid part")
        if not all(math.isfinite(value) for vertex in self.support_polygon_xy_m for value in vertex):
            raise ValueError("support polygon must be finite")
        if any(part.orientation_object_xyzw != (0.0, 0.0, 0.0, 1.0) for part in self.parts):
            raise ValueError("the current Newton compound-box adapter requires axis-aligned parts")
        for index, first in enumerate(self.parts):
            for second in self.parts[index + 1 :]:
                overlaps = all(
                    abs(first.center_object_m[axis] - second.center_object_m[axis])
                    < first.half_extents_m[axis] + second.half_extents_m[axis] - 1.0e-12
                    for axis in range(3)
                )
                if overlaps:
                    raise ValueError(f"rigid parts overlap: {first.part_id} and {second.part_id}")
        parts_by_id = {part.part_id: part for part in self.parts}
        for surface in self.contact_surfaces:
            part = parts_by_id[surface.part_id]
            normal_axes = [
                axis for axis, value in enumerate(surface.outward_normal_object) if abs(value) > 1.0e-6
            ]
            tangent_axes = [
                axis for axis, value in enumerate(surface.tangent_object) if abs(value) > 1.0e-6
            ]
            if len(normal_axes) != 1 or not math.isclose(
                abs(surface.outward_normal_object[normal_axes[0]]), 1.0, abs_tol=1.0e-6
            ):
                raise ValueError("contact-surface normal must align with a box face axis")
            if len(tangent_axes) != 1 or not math.isclose(
                abs(surface.tangent_object[tangent_axes[0]]), 1.0, abs_tol=1.0e-6
            ):
                raise ValueError("contact-surface tangent must align with a box face axis")
            normal_axis = normal_axes[0]
            tangent_axis = tangent_axes[0]
            binormal_axis = next(axis for axis in range(3) if axis not in (normal_axis, tangent_axis))
            expected_face = (
                part.center_object_m[normal_axis]
                + surface.outward_normal_object[normal_axis] * part.half_extents_m[normal_axis]
            )
            if not math.isclose(
                surface.center_object_m[normal_axis], expected_face, abs_tol=1.0e-9
            ):
                raise ValueError("contact-surface center must lie on its referenced box face")
            for surface_axis, half_size in (
                (tangent_axis, surface.half_size_m[0]),
                (binormal_axis, surface.half_size_m[1]),
            ):
                offset = abs(
                    surface.center_object_m[surface_axis] - part.center_object_m[surface_axis]
                )
                if offset + half_size > part.half_extents_m[surface_axis] + 1.0e-9:
                    raise ValueError("contact-surface bounds exceed its referenced box face")
        return self

    def contact_surface(self, surface_id: str) -> ContactSurface:
        """Resolve an allowlisted public contact surface, failing closed."""

        try:
            return next(surface for surface in self.contact_surfaces if surface.surface_id == surface_id)
        except StopIteration as error:
            raise ValueError(f"unknown object contact surface: {surface_id}") from error

    def validate_contact_operating_point(
        self,
        *,
        surface_id: str,
        lateral_offset_m: float,
        push_height_object_m: float,
    ) -> ContactSurface:
        """Validate a tabletop push point against the selected public face."""

        surface = self.contact_surface(surface_id)
        if not math.isclose(surface.outward_normal_object[2], 0.0, abs_tol=1.0e-6) or not math.isclose(
            surface.tangent_object[2], 0.0, abs_tol=1.0e-6
        ):
            raise ValueError("tabletop push requires a vertical contact surface")
        if abs(lateral_offset_m) > surface.half_size_m[0] + 1.0e-9:
            raise ValueError("lateral contact offset lies outside the selected face")
        if (
            abs(push_height_object_m - surface.center_object_m[2])
            > surface.half_size_m[1] + 1.0e-9
        ):
            raise ValueError("push height lies outside the selected face")
        return surface

    @property
    def geometry_volume_m3(self) -> float:
        """Sum of part volumes; catalog builders guarantee parts do not overlap."""

        return sum(part.volume_m3 for part in self.parts)

    @property
    def uniform_density_centroid_m(self) -> Vec3:
        """Public centroid implied by known geometry and uniform density."""

        volume = self.geometry_volume_m3
        return tuple(
            sum(part.center_object_m[axis] * part.volume_m3 for part in self.parts) / volume
            for axis in range(3)
        )  # type: ignore[return-value]

    @property
    def geometry_hash(self) -> str:
        payload = self.model_dump_json(exclude_none=False)
        return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"
