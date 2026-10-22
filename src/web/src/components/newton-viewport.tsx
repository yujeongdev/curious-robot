import { Grid, OrbitControls } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import { Camera, Clock3, Eye, RotateCcw, TriangleAlert } from "lucide-react";
import { Suspense } from "react";
import type { VisualFramePayload } from "../generated/agent-events";
import type { WorldScene } from "../state/run-reducer";
import { useRunStore } from "../state/run-store";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Tooltip } from "./ui/tooltip";
import { ContactOverlays } from "./scene/contact-overlays";
import { findObjectBody, resolveObjectVisual } from "./scene/body-mapping";
import { FrankaMirror } from "./scene/franka";

function Scene({ frame, cameraMode }: { frame: VisualFramePayload; cameraMode: "guided" | "manual" }) {
  const object = resolveObjectVisual(frame);
  return (
    <>
      <color attach="background" args={["#0a0e13"]} />
      <ambientLight intensity={0.9} />
      <directionalLight position={[0.3, 2, 2]} intensity={3.5} castShadow />
      {cameraMode === "manual" && <OrbitControls makeDefault target={[-0.12, 0.32, 0]} enableDamping={false} />}
      <group rotation={[-Math.PI / 2, 0, 0]}>
        <Grid args={[2.4, 2.4]} position={[0, 0, 0.002]} rotation={[Math.PI / 2, 0, 0]} cellSize={0.1} cellColor="#273642" sectionSize={0.5} sectionColor="#425363" fadeDistance={3} infiniteGrid />
        <mesh position={[0.22, 0, -0.035]} receiveShadow>
          <boxGeometry args={[1.45, 0.85, 0.07]} />
          <meshStandardMaterial color="#26333d" roughness={0.72} />
        </mesh>
        <Suspense fallback={null}>
          <FrankaMirror bodies={frame.bodies} toolTipRadiusM={frame.tool_tip_radius_m ?? undefined} />
        </Suspense>
        {object && (
          <group position={object.body.position_m} quaternion={object.body.orientation_xyzw}>
            {object.parts.map((part) => (
              <mesh key={part.part_id} position={part.center_object_m} quaternion={part.orientation_object_xyzw} castShadow receiveShadow>
                <boxGeometry args={part.half_extents_m.map((value) => value * 2) as [number, number, number]} />
                <meshStandardMaterial color="#d7a65c" roughness={0.78} />
              </mesh>
            ))}
          </group>
        )}
        <ContactOverlays frame={frame} />
      </group>
    </>
  );
}

function SceneSummary({ frame, scene }: { frame: VisualFramePayload | null; scene: WorldScene }) {
  const object = frame ? findObjectBody(frame.bodies, frame.object_geometry?.body_id) : undefined;
  const objectLabel = frame?.object_geometry?.archetype_id ?? "object";
  return (
    <div className="sr-only" data-testid={`scene-summary-${scene}`}>
      {frame ? `${frame.scene} scene, frame ${frame.frame_id}. ${objectLabel} at ${object?.position_m.join(", ") ?? "unknown"} meters. ${frame.contacts.length} contacts. ${frame.target_region?.geometry ? `${frame.target_region.geometry.archetype_id} shaped target` : "Target region"} ${frame.target_region ? "visible" : "absent"}.` : "No visual frame received."}
    </div>
  );
}

export function NewtonViewport({
  frame,
  scene,
  title,
  frameSequence,
  active = false,
  heldMessage,
  disconnected = false,
}: {
  frame: VisualFramePayload | null;
  scene: WorldScene;
  title: string;
  frameSequence?: number;
  active?: boolean;
  heldMessage?: string;
  disconnected?: boolean;
}) {
  const cameraMode = useRunStore((state) => state.cameraModes[scene]);
  const setCameraMode = useRunStore((state) => state.setCameraMode);
  const headingId = `viewport-title-${scene}`;
  const worldLabel = scene === "truth" ? "Reality Stand-in" : "Learned Newton Twin";
  return (
    <section className={`viewport viewport--${scene}`} aria-labelledby={headingId} data-active={active}>
      <header className="viewport-toolbar">
        <div>
          <span className="eyebrow">{scene === "truth" ? "Hidden physics authority · simulated hardware" : "Evidence-estimated physics only"}</span>
          <h2 id={headingId}>{worldLabel}</h2>
          <span className="world-purpose">{title}</span>
        </div>
        <div className="viewport-badges">
          <Badge className={scene === "belief" ? "badge--belief" : ""}>{active ? "ADVANCING" : frame ? "HELD" : "WAITING"}</Badge>
          {frame && <Badge className="mono">FRAME {frame.frame_id}</Badge>}
          <Tooltip label={cameraMode === "guided" ? "Camera follows the active stage" : "Stage changes cannot move the camera"}>
            <Button size="compact" variant="ghost" onClick={() => setCameraMode(scene, cameraMode === "guided" ? "manual" : "guided")}>
              {cameraMode === "guided" ? <Camera /> : <RotateCcw />} <span className="camera-label">{cameraMode === "guided" ? "Guided camera" : "Return to guided"}</span>
            </Button>
          </Tooltip>
        </div>
      </header>
      <div className="world-provenance mono" aria-label={`${worldLabel} provenance`}>
        <span>{frame ? frame.world_instance_id.slice(0, 12) : "world pending"}</span>
        <span>{frame?.scenario_id ?? (scene === "truth" ? "observations only · truth sealed" : "posterior rollout")}</span>
        <span><Clock3 /> {frame ? `${frame.sim_time_s.toFixed(2)} s` : "—"}</span>
        {scene === "belief" && <span>REV {frame?.belief_revision ?? "—"}</span>}
        {frameSequence !== undefined && <span>SEQ {frameSequence}</span>}
      </div>
      <div className="canvas-shell" data-frame-id={frame?.frame_id ?? ""} data-world-scene={scene}>
        {frame ? (
          <Canvas shadows dpr={[1, 1.5]} frameloop="demand" camera={{ position: [0.95, 1.05, 1.5], fov: 40 }} onCreated={({ camera }) => camera.lookAt(-0.12, 0.32, 0)} gl={{ antialias: true, alpha: false }}>
            <Scene frame={frame} cameraMode={cameraMode} />
          </Canvas>
        ) : (
          <div className="viewport-empty">
            <Eye />
            <strong>{scene === "truth" ? "Waiting for Reality stand-in observations" : "Waiting for a posterior Twin build"}</strong>
            <span>{scene === "truth" ? "The robot may experiment here, but hidden parameters remain sealed." : "Counterfactual experiments and policy rollouts appear only after evidence updates the belief."}</span>
            <ul><li>Independent world</li><li>Newton frames</li><li>No browser physics</li></ul>
          </div>
        )}
        {disconnected && (
          <div className="viewport-overlay" role="alert">
            <TriangleAlert />
            <strong>Frame stream paused</strong>
            <span>Last acknowledged frame remains visible. Physics worker status is reported separately.</span>
          </div>
        )}
        {!active && frame && !disconnected && (
          <div className="world-held-marker" role="status">
            {heldMessage ?? "Retained at the current causal cursor"}
          </div>
        )}
        <div className="viewport-legend" aria-hidden="true">
          <span><i className="legend-force" /> measured force</span>
          <span><i className="legend-target" /> goal pose</span>
          <span><i className="legend-contact" /> contact</span>
        </div>
      </div>
      <SceneSummary frame={frame} scene={scene} />
    </section>
  );
}
