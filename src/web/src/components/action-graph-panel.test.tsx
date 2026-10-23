import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { demoEvents } from "../fixtures/demo-run";
import { reduceRunEvents, seekRun } from "../state/run-reducer";
import { ActionGraphPanel } from "./action-graph-panel";

describe("ActionGraphPanel", () => {
  afterEach(cleanup);

  it("shows recorded current execution without exposing the full contract by default", () => {
    const entered = demoEvents.find(
      (event) => event.type === "action.node_transitioned" && event.payload.node_id === "motion_01_contact_push" && event.payload.state === "entered",
    )!;
    render(<ActionGraphPanel run={seekRun(demoEvents, entered.sequence)} />);

    expect(screen.getByRole("heading", { name: "Action graph" })).toBeVisible();
    expect(screen.getByText("Safe stop")).toBeVisible();
    expect(within(screen.getByRole("listitem", { current: "step" })).getByText("contact push")).toBeVisible();
    expect(screen.queryByText("action-graph-v1")).not.toBeInTheDocument();
  });

  it("reveals the success spine and explicit terminal branches on request", async () => {
    const user = userEvent.setup();
    render(<ActionGraphPanel run={reduceRunEvents(demoEvents)} />);
    await user.click(screen.getByRole("button", { name: /inspect full graph/i }));

    expect(screen.getByRole("list", { name: "Full action graph success path" })).toBeVisible();
    expect(screen.getByText("Verify goal → Return to agent")).toBeVisible();
    expect(screen.getByText("Operational node → Safe stop")).toBeVisible();
    expect(screen.getByText("action-graph-v1")).toBeVisible();
    expect(screen.getByText(/hardware · cartesian pose · 5\.0 s/i)).toBeVisible();
    expect(screen.getAllByText(/cartesian_pose · force_guard · contact_monitor/i)).toHaveLength(3);
  });

  it("renders a compact honest empty state before graph admission", () => {
    render(<ActionGraphPanel run={seekRun(demoEvents, 1)} />);
    expect(screen.getByText("NOT ADMITTED")).toBeVisible();
    expect(screen.getByText(/has not published an execution graph/i)).toBeVisible();
  });

  it("renders recorded off-spine terminals instead of falling back to the entry node", () => {
    const completed = reduceRunEvents(demoEvents);
    const safeStop = {
      ...completed,
      activeActionNodeId: "terminal_safe_stop",
      lastActionNodeId: "terminal_safe_stop",
      actionNodeStates: { ...completed.actionNodeStates, terminal_safe_stop: "entered" as const },
    };
    render(<ActionGraphPanel run={safeStop} />);
    expect(screen.getByText("TERMINAL BRANCH")).toBeVisible();
    expect(within(screen.getByRole("listitem", { current: "step" })).getByText("Safe stop")).toBeVisible();
    expect(screen.queryByText("Observe object pose", { selector: ".action-graph-summary strong" })).not.toBeInTheDocument();
  });

  it("uses unique accessible title IDs for multiple inspector instances", () => {
    const run = reduceRunEvents(demoEvents);
    render(<><ActionGraphPanel run={run} /><ActionGraphPanel run={run} /></>);
    const ids = screen.getAllByRole("heading", { name: "Action graph" }).map((heading) => heading.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("shows a latched graph contract error to the operator", () => {
    const run = { ...reduceRunEvents(demoEvents), actionGraphContractError: "Unknown action graph node: corrupted_node" };
    render(<ActionGraphPanel run={run} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Unknown action graph node");
  });
});
