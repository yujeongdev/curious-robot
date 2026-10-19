import { useGLTF } from "@react-three/drei";
import { useMemo } from "react";
import type { Mesh, Object3D } from "three";
import type { BodyTransform } from "../../generated/agent-events";
import { isFrankaBody } from "./body-mapping";

const officialVisualBodyIds = new Set([
  "fr3__fr3_link0", "fr3__fr3_link1", "fr3__fr3_link2", "fr3__fr3_link3",
  "fr3__fr3_link4", "fr3__fr3_link5", "fr3__fr3_link6", "fr3__fr3_link7",
  "fr3__fr3_hand", "fr3__fr3_leftfinger", "fr3__fr3_rightfinger",
]);
const officialFrankaGlb = "/models/franka/fr3.glb";

useGLTF.preload(officialFrankaGlb);

function OfficialFrankaLink({ body, source }: { body: BodyTransform; source: Object3D }) {
  const visual = useMemo(() => {
    const clone = source.clone(true);
    clone.traverse((object: Object3D) => {
      const mesh = object as Mesh;
      if (mesh.isMesh) {
        mesh.castShadow = true;
        mesh.receiveShadow = true;
      }
    });
    return clone;
  }, [source]);
  return (
    <group position={body.position_m} quaternion={body.orientation_xyzw} name={body.asset_node_id}>
      <primitive object={visual} dispose={null} />
    </group>
  );
}

export function FrankaMirror({ bodies }: { bodies: readonly BodyTransform[] }) {
  const { scene } = useGLTF(officialFrankaGlb);
  const robotBodies = bodies.filter(isFrankaBody);
  return (
    <group aria-label="Official Franka FR3 presentation mirror">
      {robotBodies.map((body) => {
        const source = officialVisualBodyIds.has(body.body_id) ? scene.getObjectByName(body.body_id) : null;
        return source ? <OfficialFrankaLink key={body.body_id} body={body} source={source} /> : null;
      })}
    </group>
  );
}
