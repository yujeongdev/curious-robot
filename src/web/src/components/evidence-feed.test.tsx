import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { demoEvents } from "../fixtures/demo-run";
import { reduceRunEvents } from "../state/run-reducer";
import { EvidenceFeed } from "./evidence-feed";

describe("EvidenceFeed", () => {
  it("makes physical belief change the dominant inspectable event", () => {
    render(<EvidenceFeed run={reduceRunEvents(demoEvents)} />);
    expect(screen.getByText("Synthetic preview loaded")).toBeInTheDocument();
    expect(screen.getByText(/No Newton episode was executed for this preview/i)).toBeInTheDocument();
    expect(screen.getAllByText(/belief updated/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/censored breakaway interval/i)).toBeInTheDocument();
    expect(screen.getByText(/task verified/i)).toBeInTheDocument();
    expect(screen.queryByText(/chain.of.thought/i)).not.toBeInTheDocument();
  });
});
