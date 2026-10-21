# Self-Building Laboratory spine

> 인접 문서: [아키텍처](../architecture.md) ·
> [Reality–belief 경계](2026-10-21-reality-sysid-belief-boundary.md)

2026-10-21 작성. 이 문서는 Curious Robot을 명시적인
Real-to-Sim-to-Real 실행기로 옮기기 위한 안정 계약을 정의한다. Self-Building
Labs의 [프로젝트 설명](https://self-building-labs.github.io/)과
[논문](https://arxiv.org/html/2610.23826v1)을 구조적 참고로 사용하되, 이 저장소의
현재 구현 범위와 검증 상태를 별도로 명시한다.

## Invariants

1. scientific goal은 먼저 `CapabilityRequirement`가 되고, inventory와 비교한
   `CapabilityGap`이 다음 build 단위를 결정한다.
2. Reality, Twin, Evaluator는 같은 operation vocabulary를 사용할 수 있지만 서로
   다른 endpoint와 authority를 가진다.
3. Reality와 Twin은 process, world, model, state, config identity를 공유하지 않는다.
   각 worker가 자기 OS PID로 handshake를 만들고 trusted coordinator가
   `RuntimeIdentity` 검사를 호출해야 연결할 수 있다.
4. agent process에는 Reality hidden parameter나 evaluator artifact store가 전달되지
   않는다. agent worker에는 agent-only store factory 결과만 주입하며, 공개
   artifact는 evaluator-only artifact를 provenance로 참조할 수도 없다. CAS는
   payload 의미를 보고 truth를 추측하지 않으므로 이 보장은 process capability
   격리와 typed port에서 온다.
5. Twin은 `task`, `public_geometry`, `numerical_config`, `model_parameters` 네
   section의 immutable `WorldSpec`으로 만들어진다. agent-visible PASS evidence를
   가진 `WorldSpecPatch`만 model parameters를 바꿀 수 있고 stale base patch는
   거부한다. 결과 spec은 evidence와 patch content ID를 보존한다.
6. 모든 artifact는 canonical JSON content ID, provenance, validation condition,
   PASS/FAIL outcome을 가진다. 실패한 시도도 CAS에 남는다.
7. Twin validation은 Reality 성공의 증명이 아니라 deployment gate다. 검증된
   `PolicyArtifact`만 fresh Reality episode로 보낸다.
8. Evaluator는 Reality deployment가 terminal이 된 뒤에만 실행된다. 평가 결과는
   agent authority 밖에 저장한다.
9. 향후 deterministic skill은 applicability, safety bound, recovery branch를
   명시하며 nominal path에서 LLM을 호출하지 않는다.

## State machine

```text
CREATED
  -> CAPABILITY_GAP_ASSESSED                 capability_build
  -> REALITY_EVIDENCE_CAPTURED               reality
  -> POSTERIOR_ESTIMATED                     capability_build
  -> TWIN_BUILT                              twin
  -> TWIN_VALIDATED                          twin
  -> POLICY_VALIDATED                        deployment
  -> REALITY_DEPLOYED                        reality
  -> EVALUATED                               deployment (terminal)

any non-terminal state -> FAILED             preserved FAIL artifact required
```

전이는 CAS에 실제 저장된, 직전 evidence를 직접 provenance로 갖는 정확한 kind의
PASS evidence를 요구한다. 실패 전이는 반대로 CAS의 FAIL artifact를 요구한다.
잘못된 순서나 terminal 이후 전이는 state와 history를 바꾸지 않고 거부한다. 이 네
lane 값이 차후 Agent Lab의 Reality / Twin / Deployment / Capability Build 뷰 계약이다.

## Content identity and patching

Artifact ID는 payload만이 아니라 다음 envelope 전체의 canonical bytes를 해시한다.

```text
schema version + kind + visibility + outcome
+ payload + ordered provenance + validation conditions
```

따라서 같은 결과는 같은 ID를 가지며, 같은 payload라도 validation outcome이나
lineage가 다르면 다른 artifact다. `WorldSpec` 역시 caller의 mutable dictionary를
보관하지 않고 canonical snapshot을 소유한다. Patch는 `base_spec_id`가 현재 ID와
정확히 일치할 때만 적용된다.

## Authority and threat boundary

Python 객체 은닉은 보안 sandbox가 아니다. 실제 권한 경계는 다음 capability slice의
별도 worker process와 서로 다른 artifact root다. 이번 contract slice는 실제 spawn
handshake에서 secret sentinel이 노출되지 않고 PID가 다른 것을 검증하고,
coordinator가 agent에 agent store만 전달할 factory와 검증 규칙을 제공한다. 현재
discovery runner 자체의 worker migration은 아직 PR 2 범위다. 키 이름 blacklist로
truth를 찾는 방식은 권한 모델로 사용하지 않는다.

## Next capability slice

한 개의 friction-aware push를 끝까지 연결한다.

```text
Reality friction experiment
  -> TrajectoryDataset
  -> ParameterPosterior
  -> WorldSpecPatch
  -> independent Twin worker build
  -> TwinValidationReport
  -> validated PolicyArtifact
  -> fresh Reality worker episode
  -> terminal EvaluationReport
```

이때 현재 `RobotRealityAdapter`를 `RealityPort` worker 뒤로 옮기고, posterior 기반
Newton environment를 `TwinPort` worker가 새로 구성한다. 예비 FCI/ROS 2 adapter는
같은 operation schema를 구현하되 readiness와 safety guardrail이 없으면 선택되지
않는다.

## Acceptance evidence

- evaluator truth artifact의 agent store write/read/provenance 거부와 spawn
  handshake secret sentinel 비노출
- 실제 spawn PID 및 Reality/Twin world/model/state/config identity 분리; 하나라도
  같으면 거부
- JSON key 순서가 달라도 같은 deterministic artifact ID
- stale `WorldSpecPatch` 거부 및 base 불변
- validation failure를 FAIL artifact로 재조회 가능
- 저장되지 않았거나 kind/authority/lineage가 틀린 evidence와 불법/terminal state
  transition 거부 및 history 불변
- 네 UI lane 값의 schema 고정
