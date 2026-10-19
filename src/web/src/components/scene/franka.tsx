import type { BodyTransform } from "../../generated/agent-events";

function RobotLink({ body }: { body: BodyTransform }) {
  const isHand = body.body_id.includes("hand");
  return (
    <group position={body.position_m} quaternion={body.orientation_xyzw} name={body.asset_node_id}>
      <mesh castShadow receiveShadow>
        <cylinderGeometry args={[isHand ? 0.065 : 0.072, isHand ? 0.055 : 0.065, isHand ? 0.12 : 0.28, 20]} />
        <meshStandardMaterial color={isHand ? "#17212b" : "#e8ecef"} roughness={0.36} metalness={0.2} />
      </mesh>
      {!isHand && (
        <mesh position={[0, 0, 0.14]} castShadow>
          <sphereGeometry args={[0.078, 20, 16]} />
          <meshStandardMaterial color="#f1f5f7" roughness={0.3} metalness={0.18} />
        </mesh>
      )}
      {isHand && (
        <>
          <mesh position={[0, 0.06, -0.09]}><boxGeometry args={[0.025, 0.025, 0.16]} /><meshStandardMaterial color="#2a3642" /></mesh>
          <mesh position={[0, -0.06, -0.09]}><boxGeometry args={[0.025, 0.025, 0.16]} /><meshStandardMaterial color="#2a3642" /></mesh>
        </>
      )}
    </group>
  );
}

export function FrankaMirror({ bodies }: { bodies: readonly BodyTransform[] }) {
  return (
    <group aria-label="Franka presentation mirror">
      {bodies.filter((body) => body.body_id !== "cube").map((body) => <RobotLink key={body.body_id} body={body} />)}
    </group>
  );
}
