import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TaskSummary } from "../api/events";
import { NewRunRoute } from "./new-run";

const mocks = vi.hoisted(() => ({
  createRun: vi.fn(),
  loadTasks: vi.fn(),
  navigate: vi.fn(),
}));

vi.mock("../api/events", () => ({ createRun: mocks.createRun, loadTasks: mocks.loadTasks }));
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => mocks.navigate }));

const catalog: TaskSummary[] = [
  {
    task_id: "push-cube-v1",
    objective: "Move a cube",
    object_label: "Known cube",
    requires_yaw: false,
    candidate_count: 2,
    default_material_bounds: { mass_kg: [0.08, 0.45] },
  },
  {
    task_id: "push-t-bar-v1",
    objective: "Match a T-bar pose",
    object_label: "Known compound T-bar",
    requires_yaw: true,
    candidate_count: 3,
    default_material_bounds: { mass_kg: [0.3, 0.8] },
  },
  {
    task_id: "press-sponge-v1",
    objective: "Press a sponge",
    object_label: "Compressible sponge",
    requires_yaw: false,
    candidate_count: 1,
    default_material_bounds: { stiffness_pa: [100, 200] },
  },
];

function renderRoute(query = "") {
  window.history.replaceState({}, "", `/${query}`);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<StrictMode><QueryClientProvider client={client}><NewRunRoute /></QueryClientProvider></StrictMode>);
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.loadTasks.mockResolvedValue(catalog);
  mocks.createRun.mockResolvedValue({ run_id: "run-1" });
});

afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
});

describe("task loading", () => {
  it("loads a URL-selected task without starting a run", async () => {
    const user = userEvent.setup();
    renderRoute("?task=push-t-bar-v1&seed=42&device=cpu");
    await waitFor(() => expect(screen.getByRole("button", { name: "Run discovery demo" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: /Configure run/ }));
    expect(screen.getByLabelText("Task")).toHaveValue("push-t-bar-v1");
    expect(screen.getByLabelText("Minimum mass (kg)")).toHaveValue(0.3);
    expect(mocks.createRun).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Run discovery demo" }));
    await waitFor(() => expect(mocks.createRun).toHaveBeenCalledWith(expect.objectContaining({
      task_id: "push-t-bar-v1",
      seed: 42,
      device: "cpu",
      material_bounds: { mass_kg: [0.3, 0.8] },
    }), expect.anything()));
  });

  it("rejects an unknown URL task and never autoruns a fallback", async () => {
    const user = userEvent.setup();
    renderRoute("?task=missing-v1&autorun=1");
    expect(await screen.findByRole("alert")).toHaveTextContent("Unknown task: missing-v1");
    expect(screen.getByRole("button", { name: "Run discovery demo" })).toBeDisabled();
    expect(mocks.createRun).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /Configure run/ }));
    await user.selectOptions(screen.getByLabelText("Task"), "push-cube-v1");
    expect(screen.getByRole("button", { name: "Run discovery demo" })).toBeEnabled();
    expect(mocks.createRun).not.toHaveBeenCalled();
  });

  it("waits for catalog resolution and autoruns the selected task exactly once", async () => {
    let resolveCatalog!: (tasks: TaskSummary[]) => void;
    mocks.loadTasks.mockReturnValue(new Promise<TaskSummary[]>((resolve) => { resolveCatalog = resolve; }));
    renderRoute("?task=push-t-bar-v1&autorun=1");
    expect(screen.getByRole("button", { name: "Run discovery demo" })).toBeDisabled();
    expect(mocks.createRun).not.toHaveBeenCalled();
    resolveCatalog(catalog);
    await waitFor(() => expect(mocks.createRun).toHaveBeenCalledTimes(1));
    expect(mocks.createRun).toHaveBeenCalledWith(expect.objectContaining({ task_id: "push-t-bar-v1" }), expect.anything());
    await waitFor(() => expect(mocks.navigate).toHaveBeenCalled());
    expect(mocks.createRun).toHaveBeenCalledTimes(1);
  });

  it("resets task-specific overrides and displays catalog metadata for new tasks", async () => {
    const user = userEvent.setup();
    renderRoute("?task=push-cube-v1");
    await waitFor(() => expect(screen.getByRole("button", { name: "Run discovery demo" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: /Configure run/ }));
    const minimumMass = screen.getByLabelText("Minimum mass (kg)");
    await user.clear(minimumMass);
    await user.type(minimumMass, "0.2");
    await user.selectOptions(screen.getByLabelText("Task"), "push-t-bar-v1");
    expect(screen.getByLabelText("Minimum mass (kg)")).toHaveValue(0.3);
    await user.selectOptions(screen.getByLabelText("Task"), "press-sponge-v1");
    expect(screen.queryByLabelText("Minimum mass (kg)")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Schematic preview for Compressible sponge")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Run discovery demo" }));
    await waitFor(() => expect(mocks.createRun).toHaveBeenCalledWith(expect.objectContaining({
      task_id: "press-sponge-v1",
      material_bounds: { stiffness_pa: [100, 200] },
    }), expect.anything()));
  });

  it("shows catalog failures without offering fabricated task choices", async () => {
    const user = userEvent.setup();
    mocks.loadTasks.mockRejectedValue(new Error("Catalog unavailable"));
    renderRoute();
    expect(await screen.findByRole("alert")).toHaveTextContent("Catalog unavailable");
    expect(screen.getByRole("button", { name: "Run discovery demo" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Configure run/ }));
    expect(screen.getByLabelText("Task")).toBeDisabled();
    expect(screen.queryByRole("option", { name: /Known cube/ })).not.toBeInTheDocument();
    expect(mocks.createRun).not.toHaveBeenCalled();
  });
});
