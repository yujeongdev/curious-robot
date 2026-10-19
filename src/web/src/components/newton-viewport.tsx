import { Grid, OrbitControls } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import { Camera, Eye, RotateCcw, TriangleAlert } from "lucide-react";
import { Suspense } from "react";
import type { VisualFramePayload } from "../generated/agent-events";
import { useRunStore } from "../state/run-store";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Tooltip } from "./ui/tooltip";
import { ContactOverlays } from "./scene/contact-overlays";
import { findCubeBody } from "./scene/body-mapping";
import { FrankaMirror } from "./scene/franka";

function Scene({ frame, cameraMode }: { frame: VisualFramePayload; cameraMode: "guided" | "manual" }) {
  const cube = findCubeBody(frame.bodies);
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
        <Suspense fallback={null}><FrankaMirror bodies={frame.bodies} /></Suspense>
        {cube && (
          <mesh position={cube.position_m} quaternion={cube.orientation_xyzw} castShadow>
            <boxGeometry args={[0.05, 0.05, 0.05]} />
            <meshStandardMaterial color="#d7a65c" roughness={0.78} />
          </mesh>
        )}
        <ContactOverlays frame={frame} />
      </group>
    </>
  );
}

function SceneSummary({ frame }: { frame: VisualFramePayload | null }) {
  const cube = frame ? findCubeBody(frame.bodies) : undefined;
  return (
    <div className="sr-only" aria-live="polite" data-testid="scene-summary">
      {frame ? `${frame.scene} scene, frame ${frame.frame_id}. Cube at ${cube?.position_m.join(", ") ?? "unknown"} meters. ${frame.contacts.length} contacts. Target region ${frame.target_region ? "visible" : "absent"}.` : "No visual frame received."}
    </div>
  );
}

export function NewtonViewport({ frame, disconnected = false }: { frame: VisualFramePayload | null; disconnected?: boolean }) {
  const cameraMode = useRunStore((state) => state.cameraMode);
  const setCameraMode = useRunStore((state) => state.setCameraMode);
  return (
    <section className="viewport" aria-labelledby="viewport-title">
      <header className="viewport-toolbar">
        <div>
          <span className="eyebrow">Newton presentation mirror</span>
          <h2 id="viewport-title">Simulation</h2>
        </div>
        <div className="viewport-badges">
          <Badge className={frame?.scene === "belief" ? "badge--belief" : ""}>{frame?.scene?.toUpperCase() ?? "WAITING"}</Badge>
          {frame && <Badge className="mono">FRAME {frame.frame_id}</Badge>}
          <Tooltip label={cameraMode === "guided" ? "Camera follows the active stage" : "Stage changes cannot move the camera"}>
            <Button size="compact" variant="ghost" onClick={() => setCameraMode(cameraMode === "guided" ? "manual" : "guided")}>
              {cameraMode === "guided" ? <Camera /> : <RotateCcw />} {cameraMode === "guided" ? "Guided camera" : "Return to guided"}
            </Button>
          </Tooltip>
        </div>
      </header>
      <div className="canvas-shell" data-frame-id={frame?.frame_id ?? ""}>
        {frame ? (
          <Canvas shadows dpr={[1, 1.5]} frameloop="always" camera={{ position: [0.95, 1.05, 1.5], fov: 40 }} onCreated={({ camera }) => camera.lookAt(-0.12, 0.32, 0)} gl={{ antialias: true, alpha: false }}>
            <Scene frame={frame} cameraMode={cameraMode} />
          </Canvas>
        ) : (
          <div className="viewport-empty">
            <Eye />
            <strong>Authoritative frames will appear here</strong>
            <span>Start a run to observe Newton physics. The browser does not simulate dynamics.</span>
            <ul><li>GPU worker</li><li>Body manifest</li><li>Visual frame stream</li></ul>
          </div>
        )}
        {disconnected && (
          <div className="viewport-overlay" role="alert">
            <TriangleAlert />
            <strong>Frame stream paused</strong>
            <span>Last acknowledged frame remains visible. Physics worker status is reported separately.</span>
          </div>
        )}
        <div className="viewport-legend" aria-hidden="true">
          <span><i className="legend-force" /> measured force</span>
          <span><i className="legend-target" /> target region</span>
          <span><i className="legend-contact" /> contact</span>
        </div>
      </div>
      <SceneSummary frame={frame} />
    </section>
  );
}
