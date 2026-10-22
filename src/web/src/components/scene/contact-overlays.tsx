import { Edges, Line } from "@react-three/drei";
import type { VisualFramePayload } from "../../generated/agent-events";
import { resolveTargetMarker } from "./target-mapping";

function TargetOverlay({ target }: { target: NonNullable<VisualFramePayload["target_region"]> }) {
  const marker = resolveTargetMarker(target);
  return (
    <group position={target.center_m} rotation={[0, 0, target.yaw_rad]}>
      {marker.kind === "shape" ? marker.parts.map((part) => (
        <mesh
          key={part.part_id}
          position={[part.center_object_m[0], part.center_object_m[1], 0.0015]}
          quaternion={part.orientation_object_xyzw}
        >
          <boxGeometry args={[part.half_extents_m[0] * 2, part.half_extents_m[1] * 2, 0.003]} />
          <meshBasicMaterial color="#67c98a" transparent opacity={0.2} depthWrite={false} />
          <Edges color="#67c98a" threshold={15} />
        </mesh>
      )) : (
        <>
          <mesh>
            <torusGeometry args={[marker.radiusM, 0.006, 12, 48]} />
            <meshBasicMaterial color="#67c98a" />
          </mesh>
          <mesh position={[0, 0, 0.006]}>
            <torusGeometry args={[marker.radiusM * 0.78, 0.002, 8, 48]} />
            <meshBasicMaterial color="#67c98a" />
          </mesh>
        </>
      )}
      {target.yaw_tolerance_rad != null && (
        <Line
          points={[[0, 0, 0.01], [target.radius_m, 0, 0.01]]}
          color="#67c98a"
          lineWidth={3}
        />
      )}
    </group>
  );
}

export function ContactOverlays({ frame }: { frame: VisualFramePayload }) {
  return (
    <group>
      {frame.contacts.map((contact, index) => {
        const magnitudeScale = 0.06;
        const end: [number, number, number] = [
          contact.position_m[0] + contact.force_world_n[0] * magnitudeScale,
          contact.position_m[1] + contact.force_world_n[1] * magnitudeScale,
          contact.position_m[2] + contact.force_world_n[2] * magnitudeScale,
        ];
        return (
          <group key={`${contact.position_m.join("-")}-${index}`}>
            <Line points={[contact.position_m, end]} color="#39d0df" lineWidth={3} />
            <mesh position={contact.position_m}>
              <octahedronGeometry args={[0.022]} />
              <meshBasicMaterial color="#f36c6c" />
            </mesh>
          </group>
        );
      })}
      {frame.vectors.map((vector) => {
        const forceScale = vector.units === "N" ? 0.06 : 1;
        const end: [number, number, number] = [
          vector.origin_m[0] + vector.vector[0] * forceScale,
          vector.origin_m[1] + vector.vector[1] * forceScale,
          vector.origin_m[2] + vector.vector[2] * forceScale,
        ];
        const measured = vector.kind === "measured_force";
        const commanded = vector.kind === "commanded_force";
        return (
          <Line
            key={vector.vector_id}
            points={[vector.origin_m, end]}
            color={measured ? "#39d0df" : commanded ? "#f2b84b" : vector.kind === "observed" ? "#f1f5f7" : "#39d0df"}
            lineWidth={measured ? 3 : 2}
            dashed={commanded || vector.kind === "prediction"}
            dashSize={0.025}
            gapSize={0.015}
          />
        );
      })}
      {frame.target_region && <TargetOverlay target={frame.target_region} />}
    </group>
  );
}
