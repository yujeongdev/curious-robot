import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { demoEvents } from "../fixtures/demo-run";
import { reduceRunEvents } from "../state/run-reducer";
import { useRunStore } from "../state/run-store";
import { StageStepper } from "./stage-stepper";

describe("StageStepper", () => {
  beforeEach(() => useRunStore.getState().reset());

  it("renders six macro phases while preserving detailed stages", () => {
    render(<StageStepper run={reduceRunEvents(demoEvents)} />);
    const nav = screen.getByRole("navigation", { name: "Discovery phases" });
    expect(nav).toBeInTheDocument();
    for (const name of ["Observe", "Discover", "Experiment", "Calibrate", "Decide", "Execute"]) {
      expect(screen.getByRole("button", { name: new RegExp(`^${name}:`, "i") })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: /Calibrate: system identification, update simulation/i })).toBeInTheDocument();
  });
});
