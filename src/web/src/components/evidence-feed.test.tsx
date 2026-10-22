import { cleanup, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { demoEvents } from "../fixtures/demo-run";
import { reduceRunEvents } from "../state/run-reducer";
import { EvidenceFeed, eventLane } from "./evidence-feed";

describe("EvidenceFeed", () => {
  it("makes physical belief change the dominant inspectable event", () => {
    render(<EvidenceFeed run={reduceRunEvents(demoEvents)} />);
    expect(screen.getByText("Synthetic preview loaded")).toBeInTheDocument();
    expect(screen.getByText(/No Newton episode was executed for this preview/i)).toBeInTheDocument();
    expect(screen.getAllByText(/belief updated/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/censored breakaway interval/i)).toBeInTheDocument();
    expect(screen.getByText(/task verified/i)).toBeInTheDocument();
    expect(screen.getByText(/Shape max/i)).toHaveTextContent("12.0 mm");
    expect(screen.getAllByText("REALITY").length).toBeGreaterThan(0);
    expect(screen.getAllByText("TWIN").length).toBeGreaterThan(0);
    expect(screen.getAllByText("INFERENCE").length).toBeGreaterThan(0);
    expect(screen.queryByText(/chain.of.thought/i)).not.toBeInTheDocument();
  });

  it("assigns tool provenance from an explicit ownership map", () => {
    const toolEvent = demoEvents.find((event) => event.type === "tool.started")!;
    expect(eventLane(toolEvent)).toBe("REALITY");
    expect(eventLane({
      ...toolEvent,
      payload: { ...toolEvent.payload, tool_name: "simulate_candidate_action" },
    })).toBe("TWIN");
    expect(eventLane({
      ...toolEvent,
      payload: { ...toolEvent.payload, tool_name: "unknown_future_tool" },
    })).toBe("SYSTEM");
  });

  it("does not fabricate zero shape error for historical null metrics", () => {
    cleanup();
    const historicalEvents = demoEvents.map((event) => event.type === "verification.completed"
      ? {
          ...event,
          payload: {
            ...event.payload,
            maximum_registered_corner_error_m: null,
            rms_registered_corner_error_m: null,
          },
        }
      : event);

    render(<EvidenceFeed run={reduceRunEvents(historicalEvents)} />);

    expect(screen.queryByText(/Shape max/i)).not.toBeInTheDocument();
  });
});
