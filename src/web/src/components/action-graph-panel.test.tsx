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
  });

  it("renders a compact honest empty state before graph admission", () => {
    render(<ActionGraphPanel run={seekRun(demoEvents, 1)} />);
    expect(screen.getByText("NOT ADMITTED")).toBeVisible();
    expect(screen.getByText(/has not published an execution graph/i)).toBeVisible();
  });
});
