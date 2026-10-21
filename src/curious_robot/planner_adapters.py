"""Planner adapters select validated high-level tools, never physics timesteps."""

from __future__ import annotations

import json
import os
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from .schemas import ExperimentPlan


class PlannerAdapter(Protocol):
    def choose_experiment(self, state: dict[str, object], baseline: ExperimentPlan) -> str | None: ...


class DeterministicPlannerAdapter:
    def choose_experiment(self, state: dict[str, object], baseline: ExperimentPlan) -> str | None:
        del state
        return baseline.selected


class LLMExperimentChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_id: str | None
    rationale: str


class OpenAIPlannerAdapter:
    """Optional structured chooser constrained to baseline-validated candidates."""

    def __init__(self, model: str = "gpt-5-mini"):
        from openai import OpenAI

        self.client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        self.model = model

    def choose_experiment(self, state: dict[str, object], baseline: ExperimentPlan) -> str | None:
        allowed = {score.candidate_id for score in baseline.scores if score.feasible}
        response = self.client.responses.parse(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "Choose only among validated experiment IDs. You select a high-level tool; "
                        "deterministic controllers own every physics timestep."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"state": state, "baseline": baseline.model_dump(mode="json")}, sort_keys=True
                    ),
                },
            ],
            text_format=LLMExperimentChoice,
        )
        choice = response.output_parsed.experiment_id
        if choice is not None and choice not in allowed:
            raise ValueError(f"LLM selected unvalidated experiment {choice!r}")
        return choice
