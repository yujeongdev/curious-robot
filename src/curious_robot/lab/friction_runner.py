"""Trusted friction-slice launcher and user-facing terminal reports."""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Any

from .artifact_store import LaboratoryArtifactStores
from .friction_capability import FrictionCapabilityPipeline, FrictionCapabilityResult
from .newton_ports import NewtonLearnedTwin, NewtonSimulatedReality, TerminalEvaluator
from .pipeline import LaboratoryPipeline, PipelineState
from .ports import RuntimeIdentity


def run_newton_friction_slice(
    *,
    output_directory: str | Path,
    device: str = "cuda:0",
    seed: int = 7,
    timeout_s: float = 900.0,
) -> FrictionCapabilityResult:
    """Run the one-capability pipeline across three spawned authority processes."""

    output = Path(output_directory)
    stores = LaboratoryArtifactStores(output)
    bootstrap_pipeline = LaboratoryPipeline(stores)
    capability_pipeline = None
    reality = None
    twin = None
    evaluator = None
    runtime_identities = None
    try:
        reality = NewtonSimulatedReality(
            output,
            device=device,
            seed=seed,
            timeout_s=timeout_s,
        )
        twin = NewtonLearnedTwin(output, device=device, timeout_s=timeout_s)
        evaluator = TerminalEvaluator(output, timeout_s=min(timeout_s, 60.0))
        capability_pipeline = FrictionCapabilityPipeline(
            stores=stores,
            reality=reality,
            twin=twin,
            evaluator=evaluator,
        )
        result = capability_pipeline.run()
        runtime_identities = {
            "evaluator": _identity_document(evaluator.identity),
            "reality": _identity_document(reality.identity),
            "twin": _identity_document(twin.identity),
        }
    except Exception as error:
        if capability_pipeline is None:
            failure = stores.agent.record_validation_failure(
                attempted_kind="port_startup",
                rejected_payload={"stage": "authority_process_startup"},
                errors=(type(error).__name__,),
                validation_conditions=("all authority processes return a valid handshake",),
            )
            bootstrap_pipeline.transition(PipelineState.FAILED, evidence=(failure,))
        elif capability_pipeline.pipeline.state is PipelineState.FAILED:
            failure = capability_pipeline.pipeline.history[-1].evidence[0]
        else:
            provenance = (
                capability_pipeline.pipeline.history[-1].evidence
                if capability_pipeline.pipeline.history
                else ()
            )
            failure = stores.agent.record_validation_failure(
                attempted_kind="friction_capability_runner",
                rejected_payload={"stage": capability_pipeline.pipeline.state.value},
                errors=(type(error).__name__,),
                provenance=provenance,
                validation_conditions=("capability execution reaches a terminal state",),
            )
            capability_pipeline.pipeline.transition(PipelineState.FAILED, evidence=(failure,))
        (output / "friction_capability_manifest.json").write_text(
            json.dumps(
                {
                    "failure_artifact": failure.artifact_id,
                    "failure_type": type(error).__name__,
                    "schema_version": "1.0.0",
                    "status": "FAILED",
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        raise
    finally:
        if evaluator is not None:
            evaluator.close()
        if twin is not None:
            twin.close()
        if reality is not None:
            reality.close()
    if runtime_identities is None:
        raise RuntimeError("capability completed without runtime identities")
    manifest = _build_terminal_manifest(stores, result, runtime_identities)
    (output / "friction_capability_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    _write_friction_report(output, manifest)
    return result


def _identity_document(identity: RuntimeIdentity) -> dict[str, Any]:
    return {
        "authority": identity.authority.value,
        "config_id": identity.config_id,
        "model_id": identity.model_id,
        "process_id": identity.process_id,
        "state_id": identity.state_id,
        "world_id": identity.world_id,
    }


def _build_terminal_manifest(
    stores: LaboratoryArtifactStores,
    result: FrictionCapabilityResult,
    runtime_identities: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    evaluation = stores.evaluator.get(result.evaluation_report)
    dataset = stores.agent.get(result.trajectory_dataset).payload
    posterior = stores.agent.get(result.parameter_posterior).payload
    twin_build = stores.agent.get(result.twin_build).payload
    policy = stores.agent.get(result.policy_artifact).payload
    deployment = stores.agent.get(result.reality_deployment).payload
    payloads = (dataset, posterior, twin_build, policy, deployment, evaluation.payload)
    if not all(isinstance(payload, dict) for payload in payloads):
        raise TypeError("friction capability artifacts must contain object payloads")
    return {
        "audience": "trusted_user_terminal_report",
        "artifacts": {field.name: getattr(result, field.name).artifact_id for field in fields(result)},
        "deployment": deployment,
        "evaluation": evaluation.payload,
        "policy": policy,
        "posterior": posterior,
        "runtime_identities": runtime_identities,
        "schema_version": "1.0.0",
        "world_instances": {
            "reality_deployment": deployment["world_instance_id"],
            "reality_experiment": dataset["world_instance_id"],
            "twin_validation": twin_build["world_instance_id"],
        },
    }


def _write_friction_report(output: Path, manifest: dict[str, Any]) -> None:
    belief = manifest["posterior"]["physical_belief"]
    dynamic = belief["parameters"]["mu_dynamic"]
    action = manifest["policy"]["action"]
    evaluation = manifest["evaluation"]
    worlds = manifest["world_instances"]
    runtimes = manifest["runtime_identities"]
    lines = [
        "# Friction capability report",
        "",
        "## Result",
        "",
        f"- Success: `{evaluation['success']}`",
        f"- Terminal target error: `{evaluation['target_error_m']:.6f} m`",
        f"- Selected policy: `{action['action_id']}` at `{action['speed_m_s']:.3f} m/s`",
        f"- Deterministic recovery bound: `{manifest['policy']['max_attempts']}` attempt(s)",
        f"- Reality attempts used: `{manifest['deployment']['attempt_count']}`",
        f"- Policy protocol SHA-256: `{manifest['policy']['protocol_sha256']}`",
        f"- Dynamic friction posterior: `{dynamic['mean']:.6f} ± {dynamic['std']:.6f}`",
        "",
        "## Isolation evidence",
        "",
        f"- Reality PID: `{runtimes['reality']['process_id']}`",
        f"- Twin PID: `{runtimes['twin']['process_id']}`",
        f"- Evaluator PID: `{runtimes['evaluator']['process_id']}`",
        f"- Experiment Reality world: `{worlds['reality_experiment']}`",
        f"- Twin validation world: `{worlds['twin_validation']}`",
        f"- Fresh deployment Reality world: `{worlds['reality_deployment']}`",
        "",
        "## Content-addressed lineage",
        "",
    ]
    lines.extend(f"- `{kind}`: `{artifact_id}`" for kind, artifact_id in manifest["artifacts"].items())
    lines.extend(
        [
            "",
            "> Hidden scenario parameters are absent. This report contains only public evidence, "
            "posterior state, runtime identities, and a terminal evaluator summary. It is a "
            "trusted-user report and is never injected into the agent worker.",
        ]
    )
    (output / "friction_capability_report.md").write_text("\n".join(lines) + "\n")
