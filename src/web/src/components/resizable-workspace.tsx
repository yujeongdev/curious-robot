import { useRef, useState, type PointerEvent, type ReactNode } from "react";

const STORAGE_KEY = "curious-robot.workspace-split";

function initialSplit() {
  const stored = Number(localStorage.getItem(STORAGE_KEY));
  if (stored >= 50 && stored <= 75) return stored;
  return window.innerWidth < 1280 ? 58 : 65;
}

export function ResizableWorkspace({ primary, secondary }: { primary: ReactNode; secondary: ReactNode }) {
  const root = useRef<HTMLDivElement>(null);
  const [split, setSplit] = useState(initialSplit);
  const update = (next: number) => {
    const bounded = Math.max(50, Math.min(75, next));
    setSplit(bounded);
    localStorage.setItem(STORAGE_KEY, String(bounded));
  };
  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!event.currentTarget.hasPointerCapture(event.pointerId) || !root.current) return;
    const bounds = root.current.getBoundingClientRect();
    update(((event.clientX - bounds.left) / bounds.width) * 100);
  };
  return (
    <div ref={root} className="workspace-grid" style={{ gridTemplateColumns: `minmax(0, ${split}fr) 6px minmax(350px, ${100 - split}fr)` }}>
      {primary}
      <div
        className="panel-separator"
        role="separator"
        aria-label="Simulation and evidence panel split"
        aria-orientation="vertical"
        aria-valuemin={50}
        aria-valuemax={75}
        aria-valuenow={Math.round(split)}
        tabIndex={0}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onDoubleClick={() => update(window.innerWidth < 1280 ? 58 : 65)}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") update(split - 2);
          if (event.key === "ArrowRight") update(split + 2);
          if (event.key === "Home") update(window.innerWidth < 1280 ? 58 : 65);
        }}
      ><span /></div>
      {secondary}
    </div>
  );
}
