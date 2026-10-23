# Typed ActionGraph and field deployment boundary

> 인접 문서: [Architecture](../architecture.md) · [2026 research](../research/agentic-robotics-2026.md) · [Implementation plan](../plans/2026-10-23-wrist-vision-and-franka-executor.md)

2026-10-23 작성. 범위: Curious Robot의 action graph와 실물 Franka 실행 경계.
기준: `v0.2.3` 이후 `codex/typed-action-graph` 설계.

## 1. 두 graph의 책임

`LaboratoryPipeline`은 Reality → Twin → Reality의 증거와 권한 전이를 계속 소유한다.
`ActionGraph`는 선택된 policy 아래에서 atomic robot skill의 실행 순서와 실패 경로를
소유한다. 둘을 하나의 범용 agent workflow로 합치지 않는다.

```text
Agent / planner
  → allowlisted policy
  → content-addressed ActionGraph
  → deterministic supervisor
  → native realtime transport
  → Franka safety system
```

LLM은 graph template 또는 허용된 transition만 선택한다. Newton step과 1 kHz Franka
command는 graph node 내부의 deterministic executor가 소유한다.

## 2. v1 graph contract

초기 graph는 다음 node를 포함한다.

```text
observe initial pose
  → validate observation
  → safe-plane / precontact / descent / acquire / push / retreat motion nodes
  → observe terminal pose
  → verify goal
  → completed | return-to-agent

every physical failure or safety trip → absorbing safe-stop
```

각 node는 stable ID, authority, executor, required capability, timeout, maximum attempts,
checkpoint safety, symbolic parameters를 가진다. Edge는 allowlisted outcome guard만 사용하며
임의 Python expression을 저장하지 않는다. Cycle은 traversal bound 없이는 거부한다.

Graph는 task의 private truth를 포함하지 않는다. Object ID, protocol binding,
calibration ID, public observation reference만 graph identity에 포함된다.

## 3. 현재 코드 적용

- `action_graph/contracts.py`: graph/node/edge schema, topology validator, SHA-256 ID
- `action_graph/compiler.py`: bounded Franka push phases를 graph로 컴파일
- `hardware_motion.py`: motion plan 안에 graph snapshot을 포함하고 segment와 1:1 검증
- `hardware_reality.py`: preflight와 transport result가 동일 graph ID를 attest하도록 검증
- `friction_artifacts.py`: hardware deployment evidence에 graph ID 기록

현재 concrete pylibfranka/ROS 2 transport는 없다. 따라서 이 graph는 hardware ABI와
preflight 계약까지 구현하지만 실물 실행 완료를 주장하지 않는다.

## 4. Atomic action의 의미

Atomic은 servo tick이 아니라 독립적으로 timeout, cancel, evidence, retry, safe-stop을
검증할 수 있는 최소 skill 단위다.

- `MoveJointReady`: home/ready와 recovery 전용
- `MoveCartesianPose`: free-space와 precontact 이동
- `AcquireContact`: force/contact edge 검출
- `CartesianImpedancePush`: contact 유지 구간
- `Retreat`: contact release와 safe-plane 복귀
- `Observe/Fuse/Verify`: perception과 task predicate

강화학습은 leaf executor가 아니다. 향후 graph 후보, viewpoint, contact ranking 또는
bounded residual을 제안할 수 있지만 모든 출력은 schema, Twin rollout, preflight를 거친다.

## 5. Field bundle

현장 배포 단위는 다음 identity를 묶는 content-addressed bundle이어야 한다.

- graph ID와 compiler/schema version
- task/object/public geometry/protocol IDs
- capability artifact와 validation report IDs
- robot/tool/camera calibration IDs
- required backend capabilities와 safety envelope
- compatible native executor ABI

현장 순서는 bundle hash 검증 → backend probe → calibration load → graph dry-run →
watchdog/IK/collision preflight → inactive-to-active 전환 → 실행 → node trace 저장 →
safe-stop/deactivate다.

## 6. UI

Agent Lab은 graph를 물리 권위로 사용하지 않는다. 현재 node, 선택 edge, guard evidence,
retry budget, graph ID, safe-stop path를 event stream에서 표시하는 inspector가 된다.
