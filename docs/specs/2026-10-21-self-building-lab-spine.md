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

전이는 CAS에 실제 저장된, 직전 evidence로 이어지는 provenance 계보를 갖는 정확한 kind의
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

Python 객체 은닉은 보안 sandbox가 아니다. `lab-friction`은 Reality, Twin,
Evaluator를 서로 다른 spawned process에 두고 authority별 artifact capability를
주입한다. spawn handshake에서 secret sentinel이 노출되지 않고 PID가 다른
것을 검증한다. 단, 기존 Agent Lab `run_episode` path의 worker migration은 아직
남아 있다. 키 이름 blacklist로 truth를 찾는 방식은 권한 모델로 사용하지
않는다.

## Implemented capability slice

`curious-robot lab-friction`은 한 개의 friction-aware push capability를 끝까지
연결한다.

```text
Reality static-breakaway + dynamic-slide experiment
  -> TrajectoryDataset
  -> ParameterPosterior
  -> WorldSpecPatch
  -> independent Twin worker build
  -> TwinValidationReport
  -> validated PolicyArtifact
  -> fresh Reality worker episode
  -> terminal EvaluationReport
```

Reality worker는 hidden scenario를 계속 소유하지만 실험 world를 닫고 policy
deployment용 world를 새로 만든다. Twin worker는 public trajectory에서 계산한
posterior가 적용된 `WorldSpec`만 받고, 자신이 build artifact로 보고한 바로 그 Newton
world에서 fixed push를 검증한다. Evaluator worker는 terminal deployment 이후 공개
goal predicate와 safety 결과만 평가한다. terminal evaluation 전의 artifact/schema/worker
실패는 FAIL artifact로 보존하고 FSM을 terminal `FAILED`로 바꾼다. `EVALUATED`
후 생성하는 trusted-user manifest/report는 과학적 FSM의 일부가 아니므로, 출력
실패가 완료된 evaluation을 나중에 `FAILED`로 바꾸지 않는다.

WorldSpec과 PolicyArtifact는 실행할 physical experiment protocol snapshot과 SHA-256를
같이 소유한다. Twin은 그 snapshot으로 validation하고 Reality는 policy의
hash를 다시 검사한 뒤 같은 protocol로 deployment한다. Twin validation PASS는
force/tilt feasibility뿐 아니라 task target radius 도달을 요구한다. Reality
safety stop은 유효한 terminal deployment record로 보존하되 mission success는 false로
평가된다.

현재 PolicyArtifact의 recovery branch는 최대 2회의 동일한 bounded push다.
각 실행 후 공개 pose로 target radius 진입을 검사하고 성공하면 즉시
종료하며, safety guardrail이 발화하면 추가 명령을 보내지 않는다. Twin에서도
같은 분기와 최대 횟수를 검증한 뒤만 policy를 발행한다.

기존 `run_episode`와 Agent Lab event/UI migration, 복수 capability scheduler, FCI/ROS 2,
skill distillation은 이 slice 범위 밖이다. 예비 FCI/ROS 2 adapter는 같은 operation
schema와 독립 safety guardrail을 구현한 뒤 `RealityPort` endpoint로 연결한다.

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
