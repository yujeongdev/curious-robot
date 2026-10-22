import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { demoEvents } from "../fixtures/demo-run";
import { reduceRunEvents } from "../state/run-reducer";
import { AgentCausalBridge, deriveTwinCandidates, TwinCandidateFleet } from "./agent-causal-bridge";

describe("AgentCausalBridge", () => {
  it("derives the physical evidence handoff without exposing hidden truth", () => {
    const run = reduceRunEvents(demoEvents);
    render(<AgentCausalBridge run={run} />);
    expect(screen.getByText("Reality experiment")).toBeInTheDocument();
    expect(screen.getByText("Observation boundary")).toBeInTheDocument();
    expect(screen.getByText("Posterior → Twin patch")).toBeInTheDocument();
    expect(screen.getByText("Candidate validation")).toBeInTheDocument();
    expect(screen.getByText("Fresh Reality deploy")).toBeInTheDocument();
    expect(screen.getByText("sliding-sample")).toBeInTheDocument();
    expect(screen.getByText("2 recorded Twin rollouts")).toBeInTheDocument();
    expect(screen.queryByText(/ground truth mass/i)).not.toBeInTheDocument();
  });

  it("shows only recorded candidates and leaves unused simulation capacity explicit", () => {
    const run = reduceRunEvents(demoEvents);
    const candidates = deriveTwinCandidates(run);
    expect(candidates.map((candidate) => candidate.id)).toEqual(["push_straight_low", "pick_and_place"]);
    expect(candidates[0]?.status).toBe("SELECTED");
    expect(candidates[1]?.status).toBe("REJECTED");

    render(<TwinCandidateFleet run={run} />);
    const fleet = screen.getByRole("region", { name: "Twin candidate fleet" });
    expect(within(fleet).getByText("push straight low")).toBeInTheDocument();
    expect(within(fleet).getByText("pick and place")).toBeInTheDocument();
    expect(within(fleet).getByText("No recorded candidate")).toBeInTheDocument();
    expect(within(fleet).getByText("Recorded events only · up to 3 visible")).toBeInTheDocument();
  });

  it("retains causal handoff summaries after detailed events leave the bounded window", () => {
    const complete = reduceRunEvents(demoEvents);
    const run = { ...complete, events: [] };
    const view = render(<AgentCausalBridge run={run} />);
    expect(within(view.container).getByText("probe_dynamic_friction")).toBeInTheDocument();
    expect(within(view.container).getByText("sliding-sample")).toBeInTheDocument();
    expect(within(view.container).getByText(/revision 3/i)).toBeInTheDocument();
    expect(within(view.container).getByText("push_straight_low")).toBeInTheDocument();
  });

  it("keeps candidate world identity inspectable", () => {
    const prediction = demoEvents.find((event) => event.type === "prediction.completed")!;
    const events = demoEvents.map((event) => event === prediction ? {
      ...event,
      payload: {
        ...event.payload,
        world_instance_id: "twin-world-candidate-01",
        belief_revision: 3,
      },
    } : event);
    const candidate = deriveTwinCandidates(reduceRunEvents(events))[0]!;
    expect(candidate.worldId).toBe("twin-world-candidate-01");
    expect(candidate.beliefRevision).toBe("rev 3");
  });

  it("caps the visible fleet at three while retaining the selected candidate", () => {
    const run = reduceRunEvents(demoEvents);
    const prediction = run.predictionEvents[0]!;
    const extra = (id: string, sequence: number) => ({
      ...prediction,
      event_id: `extra-${id}`,
      sequence,
      payload: { ...prediction.payload, action_id: id, scenario_id: id },
    });
    const candidates = deriveTwinCandidates({
      ...run,
      predictionEvents: [extra("candidate_before", 100), ...run.predictionEvents, extra("candidate_after", 101)],
    });
    expect(candidates).toHaveLength(3);
    expect(candidates[0]?.id).toBe("push_straight_low");
  });

  it("does not mark selection as fresh deployment before execution evidence", () => {
    const selectedIndex = demoEvents.findIndex((event) => event.type === "action.selected");
    const run = reduceRunEvents(demoEvents.slice(0, selectedIndex + 1));
    const view = render(<AgentCausalBridge run={run} />);
    const deployStep = within(view.container).getByText("Fresh Reality deploy").closest("li");
    expect(deployStep).toHaveTextContent("selected · awaiting Reality");
    expect(deployStep).toHaveTextContent("WAITING");
  });
});
