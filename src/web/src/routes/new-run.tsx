import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { ChevronDown, Cpu, FlaskConical, Play, RotateCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createRun, loadTasks, type CreateRunInput, type TaskSummary } from "../api/events";
import { Button } from "../components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "../components/ui/collapsible";

const defaults: CreateRunInput = {
  task_id: "",
  seed: 7,
  device: "cuda:0",
  include_robot: true,
  recording: true,
  evaluator_mode: false,
  material_bounds: {},
};

function configurationForTask(config: CreateRunInput, task: TaskSummary): CreateRunInput {
  return {
    ...config,
    task_id: task.task_id,
    material_bounds: { ...task.default_material_bounds, ...config.material_bounds },
  };
}

export function NewRunRoute() {
  const navigate = useNavigate();
  const [params] = useState(() => new URLSearchParams(window.location.search));
  const [config, setConfig] = useState<CreateRunInput>(() => ({
    ...defaults,
    task_id: params.get("task") ?? defaults.task_id,
    seed: Number(params.get("seed") ?? defaults.seed),
    device: params.get("device") ?? defaults.device,
  }));
  const [customOpen, setCustomOpen] = useState(false);
  const autorunStarted = useRef(false);
  const tasks = useQuery({ queryKey: ["task-catalog"], queryFn: loadTasks });
  const mutation = useMutation({
    mutationFn: createRun,
    onSuccess: ({ run_id }) => navigate({ to: "/runs/$runId", params: { runId: run_id } }),
  });
  const { mutate } = mutation;
  const catalog = tasks.isSuccess ? tasks.data : [];
  const selectedTask = catalog.find((task) => task.task_id === (config.task_id || catalog[0]?.task_id));
  const loadedConfig = selectedTask ? configurationForTask(config, selectedTask) : config;
  const massBounds = loadedConfig.material_bounds.mass_kg as [number, number] | undefined;
  const unknownTask = tasks.isSuccess && config.task_id !== "" && !selectedTask;

  useEffect(() => {
    if (params.get("autorun") !== "1" || autorunStarted.current || !tasks.isSuccess || !tasks.data.length) return;
    const requestedTaskId = params.get("task") || tasks.data[0]?.task_id;
    const task = tasks.data.find((item) => item.task_id === requestedTaskId);
    if (!task) return;
    autorunStarted.current = true;
    mutate(configurationForTask({
      ...defaults,
      seed: Number(params.get("seed") ?? defaults.seed),
      device: params.get("device") ?? defaults.device,
    }, task));
  }, [mutate, params, tasks.data, tasks.isSuccess]);

  const runDemo = () => {
    if (selectedTask) mutate(loadedConfig);
  };
  const previewFixture = () => navigate({ to: "/runs/$runId", params: { runId: "demo" }, search: { fixture: true } });
  return (
    <main className="new-run">
      <header className="new-run-header">
        <a className="brand" href="/" aria-label="Curious Robot home"><span className="brand-mark">CR</span><span>Curious Robot<small>Agent Lab</small></span></a>
        <span className="status-line"><i /> LOCAL WORKSTATION</span>
      </header>
      <section className="mission-panel">
        <div className="mission-copy">
          <span className="eyebrow">Discover physics · simulate · act · learn</span>
          <h1>Watch a robot turn contact into physical knowledge.</h1>
          <p>Franka does not receive the object’s mass or surface friction. It experiments in the Reality stand-in, patches evidence-backed parameters into independent Newton Twins, then deploys the selected controller back to fresh Reality.</p>
          <div className="mission-actions">
            <Button variant="primary" onClick={runDemo} disabled={mutation.isPending || !selectedTask}>
              {mutation.isPending ? <RotateCw className="spin" /> : <Play />} {mutation.isPending ? "Preparing Newton…" : "Run discovery demo"}
            </Button>
            <Button variant="secondary" onClick={previewFixture}><FlaskConical /> Preview synthetic example</Button>
          </div>
          {tasks.isPending && <p className="preset-line" role="status">Loading task catalog…</p>}
          {tasks.isError && (
            <div className="inline-error" role="alert">
              <strong>Could not load the task catalog.</strong>
              <span>{tasks.error.message}</span>
            </div>
          )}
          {unknownTask && (
            <div className="inline-error" role="alert">
              <strong>Unknown task: {config.task_id}</strong>
              <span>Choose an available task in Configure run.</span>
            </div>
          )}
          {tasks.isSuccess && tasks.data.length === 0 && (
            <div className="inline-error" role="alert">No tasks are available in the task catalog.</div>
          )}
          {mutation.isError && (
            <div className="inline-error" role="alert">
              <strong>Could not reach the Newton control plane.</strong>
              <span>{mutation.error.message}</span>
              <Button variant="secondary" size="compact" onClick={previewFixture}>Open synthetic preview</Button>
            </div>
          )}
          <p className="preset-line"><Cpu /> Task <strong>{loadedConfig.task_id || "Select a task"}</strong> · seed {loadedConfig.seed} · {loadedConfig.device} · recording on</p>
        </div>
        <div className="preview-scene" aria-label={`Schematic preview for ${selectedTask?.object_label ?? "selected task"}`}>
          <div className="preview-grid" />
          <div className="preview-robot"><i /><i /><i /><i /></div>
          <div className={`preview-cube ${loadedConfig.task_id === "push-t-bar-v1" ? "preview-tbar" : ""}`}>?</div>
          <div className="preview-target"><span>TARGET</span></div>
          <div className="preview-label">TASK <strong>SCHEMATIC</strong></div>
        </div>
      </section>
      <section className="journey-strip" aria-label="Discovery journey">
        <span><b>01</b><strong>Observe</strong><small>Known geometry</small></span>
        <span><b>02</b><strong>Probe</strong><small>Bounded interaction</small></span>
        <span><b>03</b><strong>Infer</strong><small>Belief contracts</small></span>
        <span><b>04</b><strong>Decide</strong><small>Action candidates</small></span>
        <span><b>05</b><strong>Verify</strong><small>Prediction residual</small></span>
      </section>
      <Collapsible open={customOpen} onOpenChange={setCustomOpen} className="config-panel">
        <CollapsibleTrigger className="config-trigger"><span><span className="eyebrow">Research controls</span><strong>Configure run</strong></span><ChevronDown /></CollapsibleTrigger>
        <CollapsibleContent>
          <div className="config-grid">
            <label>Task<select value={loadedConfig.task_id} disabled={!catalog.length} onChange={(event) => setConfig({ ...config, task_id: event.target.value, material_bounds: {} })}>
              {!selectedTask && <option value={config.task_id}>{unknownTask ? `Unknown task: ${config.task_id}` : "Select a task"}</option>}
              {catalog.map((task) => <option key={task.task_id} value={task.task_id}>{task.object_label} · {task.task_id}</option>)}
            </select></label>
            <label>Random seed<input type="number" value={config.seed} onChange={(event) => setConfig({ ...config, seed: Number(event.target.value) })} /></label>
            <label>Device<select value={config.device} onChange={(event) => setConfig({ ...config, device: event.target.value })}><option>cuda:0</option><option>cpu</option></select></label>
            <label className="check-row"><input type="checkbox" checked={config.include_robot} onChange={(event) => setConfig({ ...config, include_robot: event.target.checked })} /> Full Franka articulation</label>
            <label className="check-row"><input type="checkbox" checked={config.recording} onChange={(event) => setConfig({ ...config, recording: event.target.checked })} /> Record replay artifacts</label>
            <label>Surface family<select value={String(loadedConfig.material_bounds.surface_family ?? "random")} onChange={(event) => setConfig({ ...config, material_bounds: { ...config.material_bounds, surface_family: event.target.value === "random" ? null : event.target.value } })}><option value="random">Random material</option><option value="wood">Wood</option><option value="rubber_mat">Rubber mat</option><option value="coated_metal">Coated metal</option><option value="polymer">Polymer</option></select></label>
            {massBounds && <label>Minimum mass (kg)<input type="number" min="0.02" max="2" step="0.01" value={massBounds[0]} onChange={(event) => setConfig({ ...config, material_bounds: { ...config.material_bounds, mass_kg: [Number(event.target.value), massBounds[1]] } })} /></label>}
            {massBounds && <label>Maximum mass (kg)<input type="number" min="0.02" max="2" step="0.01" value={massBounds[1]} onChange={(event) => setConfig({ ...config, material_bounds: { ...config.material_bounds, mass_kg: [massBounds[0], Number(event.target.value)] } })} /></label>}
            <label className="check-row"><input type="checkbox" checked={config.evaluator_mode} onChange={(event) => setConfig({ ...config, evaluator_mode: event.target.checked })} /> Reveal truth after terminal state</label>
          </div>
          <p className="desktop-note">Advanced material bounds and capture configuration are available on desktop.</p>
        </CollapsibleContent>
      </Collapsible>
    </main>
  );
}
