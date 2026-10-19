import { Line } from "@react-three/drei";
import type { VisualFramePayload } from "../../generated/agent-events";

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
      {frame.target_region && (
        <group position={frame.target_region.center_m}>
          <mesh rotation={[0, 0, 0]}>
            <torusGeometry args={[frame.target_region.radius_m, 0.006, 12, 48]} />
            <meshBasicMaterial color="#67c98a" />
          </mesh>
          <mesh position={[0, 0, 0.006]}>
            <torusGeometry args={[frame.target_region.radius_m * 0.78, 0.002, 8, 48]} />
            <meshBasicMaterial color="#67c98a" />
          </mesh>
        </group>
      )}
    </group>
  );
}
