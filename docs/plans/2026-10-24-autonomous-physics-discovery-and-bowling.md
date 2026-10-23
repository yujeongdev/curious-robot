# 로봇이 실험으로 필요한 시뮬레이션을 만드는 개발 계획

> 인접 문서: [설계와 현재 코드 경계](../specs/2026-10-24-experiment-driven-twin-construction.md) · [Task/CLI 구조](../specs/2026-10-24-task-loading-and-curo-cli.md) · [에셋·물리·2026 선행연구](../research/simready-bowling-and-model-discovery-2026.md) · [기존 하드웨어 계획](2026-10-23-wrist-vision-and-franka-executor.md)

2026-10-24 작성. 범위: 두 Franka의 볼링을 첫 사례로, 시뮬레이션 셋업·실험·SysID·모델 선택을 자동화하는 구현 순서.
기준: `e56d6b3b95b2fb037be664027c62bcabc530edfe`; 볼링/발견 확장은 제안이며 실물 실험 미실행. CLI 기반의 구현 상태는 연결된 spec과 capability 목록에서 관리한다.

## 1. 만들려는 결과

사용자는 “핀을 세우고 공을 굴려 넘어뜨려라”라는 목표를 준다. 시스템은 다음을 수행한다.

cube/T-bar는 여러 task 중 선택 가능한 기존 예시다. 사용자는 task catalog에서 고른 task를 같은 화면·시스템에 로드한다. 볼링이나 스펀지가 새로운 앱 모드가 되지 않도록, 목표·씬·입력·실험·평가를 task binding으로 교체한다.

1. 에셋을 찾고 실제 로봇·그리퍼·작업 공간으로 가능한 씬을 계산한다.
2. 목표 달성에 영향을 주는 미지의 물리 현상을 가설로 만든다.
3. 핀을 낮게 밀고, 기울이고, 공을 굴리는 실험 중 필요한 것부터 선택한다.
4. 관측으로 질량·마찰·무게중심의 식별 가능한 부분과 불확실성을 추정한다.
5. 에이전트가 에셋 변환·배치, collision·물성·solver·센서·초기 조건을 담은 실행 설정을 만들고, 독립 Twin을 빌드·실행한다. 셋업 검증과 학습에 쓰지 않은 예측 검증을 각각 통과시킨다.
6. Twin에서 선택한 행동을 Reality에서 실행한다. 오차가 커지면 다시 실험한다.
7. 핀 세우는 로봇이 실제 접촉·파지·놓기로 복구하고, 공 굴리는 로봇이 다음 회차를 수행한다.

장기 핵심 기여는 **“무엇을 알아야 하는지, 어떤 실험으로 알 수 있는지, 어느 모델이면 충분한지를 행동 결과로 검증하는 로봇”**이다. 볼링의 성공 장면은 이 과정의 결과물이다.

시뮬레이션 셋업도 에이전트가 수행하는 핵심 작업이다. 사용자가 완성된 USD/MJCF·충돌 형상·재료·solver 설정을 미리 제공할 필요가 없도록 한다. 입력으로 주어진 목표·에셋·로봇 프로파일과 관측에서 실행 가능한 simulation recipe를 만들고, 초기화/검증 실패의 원인을 찾아 수정한 뒤 다시 실행한다. 엔진/재료 지원 밖의 기능은 명시적인 gap으로 남긴다.

`Reality(sim) → Twin(sim) → Reality(sim)`을 먼저 완성한다. 같은 계약으로 `Reality(hardware) → Twin(sim) → Reality(hardware)`를 연결한다. 앞의 결과를 실물 sim-to-real 성과로 표시하지 않는다.

## 2. 권장 아키텍처와 선택 이유

| 접근 | 작업량·위험의 상대 추정 | 장점 | 대가 | 판단 |
| --- | --- | --- | --- | --- |
| A. 볼링 전용 스크립트 | 작음 / 중간 | 가장 빠른 데모, 기존 controller 재사용 | 사람이 물성·실험·모델을 모두 정하며 스펀지로 확장하기 어려움 | 물리 기준 실험에만 사용 |
| B. 기존 Lab에 bounded discovery 추가 | 중간 / 중간 | 증거·worker·ActionGraph 재사용, 모델 선택을 별도 검증 가능 | 관측·식별성·모델 계약을 명확히 해야 함 | **권장** |
| C. 범용 world model·멀티 에이전트 플랫폼부터 구축 | 큼 / 높음 | 표현력과 장기 확장 범위가 큼 | 데이터·학습·운영 부담이 물리 발견의 검증을 가림 | 별도 근거가 생길 때 검토 |

B는 하나의 계획 에이전트와 결정론적 두 로봇 실행기를 사용한다. 로봇이 둘이라는 이유로 LLM 에이전트를 둘 만들지 않는다. 새로운 범용 workflow 엔진, 물리 엔진, 분산 서비스도 만들지 않는다.

확장 단위는 task별 거대한 subclass가 아니라 **모델 후보 + 관측 계약 + 실험 프로토콜 + 추정기 + 검증기**의 작은 등록 항목이다. 세부 계약은 [설계 §4–7](../specs/2026-10-24-experiment-driven-twin-construction.md#4-추가할-최소-계약)에 둔다.

## 3. 바로잡아야 할 전제

- SimReady는 탐색·형상·메타데이터의 시작점이다. 현장 마찰이나 무게중심의 정답을 보증하지 않는다.
- 이번 조사에서 볼링공·핀·레인으로 검증된 SimReady 에셋을 찾지 못했다. 검색 결과가 없으면 파라메트릭 형상을 사용하며 출처를 명시한다.
- Panda와 FR3의 한계를 섞지 않는다. 실제 로봇/URDF/툴/제어 인터페이스 버전을 확인한 `RobotProfile`이 필요하다.
- 최대 TCP 속도와 payload만으로 공 무게를 정하지 않는다. 바닥의 공을 미는 접촉과 공을 들어 운반하는 작업은 다른 제약을 가진다.
- Neo-Hookean은 재료 법칙, VBD는 수치 solver다. 둘은 함께 사용될 수 있다.
- 단일 밀기로 3D 무게중심·관성·모든 마찰을 동시에 식별했다고 말하지 않는다.
- 기존 평면 마찰 보정을 볼링에 적용하지 않는다. 구름·전도·충돌은 새로운 물리 검증 경로를 통과해야 한다.

## 4. 구현 순서

각 단계는 별도 PR 크기로 자른다. 아래 완료 조건은 **제안된 gate**이며 현재 달성된 성과가 아니다. GPU 물리 gate가 필요한 단계는 portable unit test만으로 닫지 않는다.

### C0 — Task 로드와 모듈별 curo CLI 기반

현재 task catalog를 CLI와 화면에서 선택하는 경계로 사용한다. `curo`는 native Click tree 조합, 공통 `Operation` 실행 경계, 재사용 option·출력 helper와 task/sim/run/web adapter를 나눈다. help·입력·capability는 같은 tree에서 생성하며, task 해석은 CLI·HTTP·setup이 공유하는 `ResolvedTask`에 둔다. 기존 `curious-robot`은 호환 경로로 유지한다.

- task 선택·조회, 화면 load, simulation setup, 전체 run을 별도 operation으로 둔다.
- 에셋·실험·SysID·모델·정책·실행 관리·artifact·실물의 command tree도 등록한다.
- 미구현 operation은 공통 `Operation` 경계에서 “아직 구현되지 않았습니다”, `status=not_implemented`, hint와 exit 3을 반환한다. task 성공·artifact를 꾸며내지 않는다.
- `capabilities --json`은 실제 Click callback 유무에서 구현 상태를 읽으며, 사람용 help와 에이전트용 JSON 계약을 유지한다.
- 전용 skill과 guide는 지원 상태를 확인한 뒤 실제 CLI를 사용하게 한다.

완료: help/catalog는 GPU 없이 동작하며, unknown task를 거절하고 task load만으로 run이 생기지 않는다. pending operation 호출은 실제 작업 없이 명확히 실패한다. 초기 구현 범위·통과한 검증과 UI/CLI pipeline 통합의 남은 작업은 [Task/CLI spec](../specs/2026-10-24-task-loading-and-curo-cli.md)에 둔다.

### P0 — 로봇 프로파일과 에셋 입장 검사

목적: 다운받은 모양을 실제 가능한 장면의 재료로 바꾼다.

- `assets/`에 검색 결과, 정확한 HF revision, license, 파일 hash와 의존성을 기록한다.
- visual mesh, collision proxy, 저자 제공 물리 prior를 분리한다. 단위·축·스케일·질량·관성의 유효성을 검사한다.
- 실제 모델을 식별하는 `RobotProfile`과 공·핀·레인 후보를 평가하는 순수 `SceneAdmission`을 추가한다.
- P0는 형상·운동학의 `candidate`까지만 만든다. 동역학 미검증 후보는 실행을 허가하지 않으며, P1–P4에서 제한된 probe/최종 deployment의 scope별 승인을 채운다.
- 불가능한 경우 `infeasible`과 이유를 반환한다. 설정을 조용히 바꾸지 않고 축소 대안을 기록한다.

완료: 고정된 에셋 manifest를 오프라인 재로딩할 수 있고, 80 mm 그리퍼로 정규 공을 측면 파지하는 후보를 거절한다. 질량만 작게 수정하거나 visual만 축소한 후보도 거절한다. 실제 HF 검색 결과가 관련 없는 rolling pin이면 볼링 핀으로 채택하지 않는다.

### P0a — 실행 가능한 시뮬레이션 셋업 자동화

의존: P0. 에셋과 목표만 있는 상태에서 agent tool 호출로 Newton world를 만들고 headless smoke rollout까지 실행한다.

첫 PR 범위는 기존 single-robot cube/table builder의 자동 구성·실행이다. 볼링 sphere/pin contact adapter는 P1, 여러 로봇/물체 구성은 P2에서 같은 setup 경로에 추가한다. 아직 등록되지 않은 볼링/스펀지 조합은 gap을 반환하며 미래 기능을 P0a의 완료 조건으로 당기지 않는다.

- 기존 dependency lock을 사용한 환경 준비, GPU/runtime 가용성 검사, 버전·device 기록을 셋업에 포함한다. 필요한 backend가 없으면 해당 항목을 반환하며 다른 backend로 조용히 바꾸지 않는다.
- versioned `SimulationRecipe`에 robot/object 인스턴스·배치/frame, collision proxy, 질량·관성·COM, contact pair/물성, drive/control, solver·time step·capacity, sensor producer, 초기화·reset·관측/기록 설정을 담는다.
- 에이전트가 허용된 builder/adapter를 조합하고 기존 WorldSpec→Newton build 경로로 compile·instantiate한다. 스펀지의 volume/tet mesh와 material adapter 셋업은 P7에서 이 경로에 추가한다.
- 로딩→단위/좌표→관통/안정성→sensor signal→짧은 stepping을 검사하고, typed 진단에 따라 예산 안에서 셋업을 수정·재빌드한다. 물성 추정 실패를 solver 설정 변경으로 덮지 않는다.
- recipe, build report, runtime manifest, probe 결과를 기존 artifact store에 남겨 동일 설정을 오프라인으로 재구성한다. UI는 성공한 world의 실제 기록만 표시한다.

완료: 수동 scene 편집 없이 초기 목표·프로파일·에셋 입력에서 world가 생성되고 smoke rollout 결과가 나온다. 잘못된 단위·누락된 collider/센서 producer·초기 관통을 각각 탐지한다. 수정 가능한 실패는 새 recipe revision으로 복구하고, 지원 밖 solver/material·예산 초과는 원인과 함께 종료한다. 이는 setup gate이며 P1의 접촉 타당성이나 투구 실행 승인을 대신하지 않는다.

Reality(sim)의 private 물성/조건은 별도 setup worker가 생성한다. 에이전트가 hidden 정답을 읽고 Twin에 복사하지 못하도록 recipe와 진단의 공개 범위를 구분한다. [상세 셋업 계약](../specs/2026-10-24-experiment-driven-twin-construction.md#11-시뮬레이션-셋업도-에이전트의-실행-작업이다)

### P1 — 공·핀의 물리 기준 실험

의존: P0/P0a. 자동 생성한 world에서 로봇 정책을 붙이기 전에 접촉 모델을 검증한다.

- sphere 공, 전도 가능한 pin collider, 평면/구역별 레인으로 작은 Newton scene을 만든다.
- 미끄럼, 순수 구름, 감속, 핀 정지 안정성, 기울이기, 단일 충돌의 기준 실험을 만든다.
- MuJoCo native contact와 rolling/torsional 설정을 확인한다. 접촉 차원과 조합 법칙을 명시한다.
- 연구 reference에는 문헌 모델의 oil `μ≈0.04` / dry `μ≈0.20`을 prior로 넣는다. 실제 설치/축소 재료의 측정값과 구분하고, episode variation은 별도 가정으로 기록한다.
- 기존 `body_f` 마찰 보정은 이 scene에서 비활성화한다. legacy cube/T-bar 경로는 기존 model ID로 유지한다.
- restitution 값을 썼다는 이유로 반발이 보정되었다고 판단하지 않는다. 실제 solver가 쓰는 접촉 응답을 낙하·충돌 데이터로 검증한다.

완료: 무접촉 감속 보정 없음, 접촉 방향·단위 정확, 접촉 overflow 없음. `dt`를 절반으로 줄인 결과와 비교하고 수치 오차가 물성 변화로 오인되지 않음을 보고한다. 상세 충돌 메시와 단순 collider의 차이를 한 번 측정한다.

### P2 — 두 로봇과 여러 물체의 씬 계약

의존: P1. `SceneSpec`으로 robot/object instance, frame, role을 표현한다.

- `pinsetter`, `roller`, `ball`, `pins[]`, `lane`, 회수 구역에 안정적인 ID를 부여한다.
- 하나의 Reality world에 두 로봇을 둔다. Twin은 독립된 별도 world다.
- 레인 사용권을 ActionGraph 실행기가 관리한다. pinsetter가 빠져나가고 정지 확인을 마친 뒤에 roller가 진입한다.
- 기존 event의 단일 geometry 필드를 versioned scene manifest로 확장한다. cube/T-bar replay용 adapter를 둔다.

완료: 여러 body/robot가 기록·replay에서 같은 ID로 추적된다. 접근권 중복·stale 관측·중단된 reset을 거절한다. 브라우저가 좌표나 충돌 결과를 계산하지 않는다.

### P3 — 관측 producer와 핀의 능동 식별

의존: P2. 처음에는 상태 기반 simulated sensor임을 명시한다. 카메라 기반 producer는 별도 gate다.

- 접촉 위치·선형 힘·핀 pose·불확실성·지연·센서 좌표계를 기록한다.
- 낮은 높이의 접선 밀기로 바닥 마찰을, 여러 방향의 bounded tilt로 안정성/COM 제약을 수집한다.
- 질량 계측과 wrist moment가 검증되기 전에는 해당 값을 prior 또는 식별 불가로 남긴다.
- 기존 world-X 추정기와 좁은 마찰/질량 hardcoded bounds를 범용 추정기로 오인하지 않도록 protocol별로 분리한다.
- 필요한 wrench producer와 파지·들기가 준비되면 질량과 여러 자세의 COM 계측을 추가한다.

완료: 에이전트가 “질량 부재로 마찰의 구간만 알 수 있다”와 “추가 센서가 필요하다”를 반환할 수 있다. 여러 방향의 보류 실험에서 핀의 slide/tip 구분을 예측한다. 추정에 쓰지 않은 evaluator truth로만 식별 오차를 평가한다.

### P4 — 공의 구름 식별과 한 핀 맞히기

의존: P3. 초기에는 수동 복구를 명시적으로 허용한다.

- 검증된 pusher로 공을 바닥에서 가속하고 분리한다. 공은 손에 붙이거나 발사하지 않는다.
- 이동 속도, 회전 속도, release 오차와 contact-point slip을 관측한다. 회전을 볼 수 없으면 관측 gap을 반환한다.
- v1의 균질 실구·중심 COM·`I/(mr²)=2/5` 가정을 기록한다. 실물/weighted-core 공은 독립 계측 또는 nuisance posterior 없이 같은 가정을 적용하지 않는다.
- 공–레인, 핀–레인, 공–핀의 상호작용 파라미터를 분리한다.
- learned Twin의 후보 rollout으로 한 핀을 넘어뜨리는 release를 고르고 fresh Reality에서 확인한다.

완료: 미끄럼 감속과 구름 저항을 분리한 예측 보고서, release 오차 보고서, 한 핀 타격의 fresh deployment artifact가 나온다. 실험 전에 정해진 조건에서의 성공률과 모델 예측 오차를 따로 공개한다.

### P5 — 고정 순서를 벗어난 discovery loop

의존: P3/P4. 여기서 과학적 핵심이 동작해야 한다.

- 기존 Lab에 `lab/discovery.py`를 추가한다. 각 iteration은 hypothesis → experiment → evidence → estimate → rebuild → validation을 기록한다.
- task 관측량의 민감도와 후보 간 예측 불일치를 이용해 다음 실험을 고른다.
- LLM은 허용된 모델·실험과 근거 ID를 제안한다. estimator·입장 검사·예산·executor는 결정론적으로 실행한다.
- low/high friction, COM 이동, rolling-law mismatch에서 다른 실험을 선택하는지 평가한다.
- 구조 비교는 B0(균일 Coulomb), B1(rolling/spin 저항 추가), B2(oil/dry 두 구역)로 한다. low/high μ·COM 변화는 parameter adaptation으로 따로 세고, 세 구역/drift 보류 조건에서는 모델 부적합 탐지를 평가한다.
- 모델 구조 변경은 새 WorldSpec lineage로 남긴다. 기존 parameter-only patch 제약을 우회하지 않는다.

완료: 같은 “핀 넘어뜨리기” 요청에서 숨긴 상황에 따라 실험 순서가 달라진다. 잘못된 모델을 기각하고 재실험하거나 `model_inadequate`로 종료한다. 고정 probe, 동일 예산 random probe, LLM 제안만 쓰는 baseline과 비교한다.

### P6 — pinsetter의 실제 복구와 여러 핀의 반복 회차

의존: P2/P4/P5 및 파지·놓기 leaf의 물리 검증.

- 핀 집기 → 수직 정렬 → 바닥 접촉 → 놓기 → 안정성 확인을 구현한다.
- 넘어진 핀과 공을 회수하고 레인을 비운다. 초기화 teleport는 benchmark episode 생성에만 허용한다.
- 1핀 → 3핀 → 10핀으로 늘린다. 회차 사이의 배치 오차·마찰 drift·복구 실패를 관측한다.
- 하나의 planner가 두 로봇의 role/lease를 선택한다. reset 중에는 투구가 불가능하다.

완료: 3핀 이상에서 pinsetter가 세우고 roller가 굴리는 연속 회차가 완주한다. 복구 실패·누락 핀·공 미회수 시 다음 회차를 중단한다. 충돌 후 pin pose와 정지 안정성으로 득점을 판정한다.

### P7 — 스펀지로 모델 선택의 전이 검증

의존: P5. 볼링과 별도 task plugin으로 구현한다.

- “허용 힘 안에서 정해진 깊이로 누르고 복원 확인”을 목표로 삼는다.
- rigid baseline과 tetrahedral stable Neo-Hookean/VBD 후보를 등록한다.
- 에이전트가 quasi-static compression, lateral strain, hold, unload, rate sweep에서 필요한 실험을 선택한다.
- 압축성·속도 의존·hysteresis가 지원 모델 밖이면 gap을 남긴다. foam/viscoelastic law를 구현하지 않고 solver 명칭만 바꿔 지원한다고 하지 않는다.
- 초기 구현은 검증된 단일 solver/구동 plate로 한다. articulated Franka와의 coupling은 양방향 힘 전달을 검증한 뒤 붙인다.

완료: 볼링 controller의 조건문을 고치지 않고 registry 등록으로 task가 추가된다. stiffness만 맞추는 것과 재료 모델 선택의 차이를 보류 하중·속도에서 측정한다. 유체가 든 스펀지 등 지원 밖 현상은 명시적으로 기각한다.

### P8 — 동일 실험 계약의 실물 Franka 연결

기존 [wrist vision/FCI 계획](2026-10-23-wrist-vision-and-franka-executor.md)의 transport·executor·camera producer를 선행 의존성으로 사용한다. P0–P7과 별도로 진행할 수 있으나 실물 완료 gate를 생략할 수 없다.

- deployment-only인 hardware Reality에 allowlisted experiment 실행·evidence 수집을 추가한다.
- 질량 tare, wrench frame, hand-eye/time alignment, tool inertia, 관측 지연을 교정한다.
- 1대에서 저속 probe와 단일 핀 실험을 통과한 뒤 두 번째 로봇·자동 복구를 연결한다.
- 실물 reset 이후의 상태를 새로 관측한다. sim처럼 새 world를 할당해 물리 복구를 대신하지 않는다.

완료: 실제 센서와 command ID가 연결된 raw evidence, 보류 실험, Twin 예측, 실물 실행 결과가 하나의 lineage를 가진다. 실물 기준 물성은 독립 계측 장비/별도 프로토콜로 검증한다. 연결된 로봇·카메라가 없으면 `hardware_unavailable`이며 simulated evidence로 대체하지 않는다.

## 5. 첫 PR과 첫 공개 데모

**볼링 확장의 첫 PR은 P0**으로 한정한다. C0는 모든 task가 공통으로 사용하는 CLI 기반이며 볼링의 물리 완료 gate와 구분한다. P0는 HF asset provenance와 실제 로봇 프로파일을 입력받아, full-size/mini 장면의 잠정 후보·거절 이유를 내는 admission report를 만든다. 보고서에는 채택 에셋, procedural 대체, 적용 제약, 미완료된 dynamics gate와 남은 unknown을 넣는다.

다음 PR인 P0a는 이 후보를 실제 Newton world로 만드는 셋업 자동화다. P0의 후보 JSON만 생성한 상태를 “시뮬레이션 자동 셋업 완료”로 표시하지 않는다.

첫 과학적 데모는 P4까지의 “수동 복구가 있는 한 핀 실험”이다. 사용자 요청의 두 로봇 완성 데모는 P6이며, 둘을 같은 완료 상태로 표시하지 않는다. 에이전트가 상황별 실험을 바꾸는 핵심 시연은 P5에서 추가된다.

완성 시연의 인과 순서는 다음과 같다.

```text
목표 입력 → 씬 제약 확인 → 시뮬레이션 자동 구성·smoke 검증
→ pinsetter 배치·퇴장
→ roller의 낮은 밀기/기울이기/구름 실험
→ 불확실성·모델 후보 변경 → 보류 예측 검증
→ Twin release 선택 → 두 로봇 퇴피/실행 → 실제 관측으로 득점
→ pinsetter 복구 → 다음 회차 또는 재식별
```

## 6. 성공 기준과 비교 실험

공통으로 “모델이 정확하다”, “task가 성공했다”, “안전 조건이 지켜졌다”, “실험 비용이 줄었다”를 각각 보고한다. 하나의 종합 점수만 남기지 않는다.

| 질문 | 비교/측정 | 출시 판단 |
| --- | --- | --- |
| 시뮬레이션도 스스로 구성하는가 | 사전 완성 scene 없이 recipe 생성·world build·smoke rollout | 수동 편집 횟수, 셋업 성공률/시간, 실패 진단·복구, 동일 설정 재구성 |
| 에이전트가 필요한 실험을 고르는가 | 고정 순서·random·LLM 제안과 같은 예산 비교 | 실험 횟수/시간 대비 보류 예측·task 성공 Pareto 개선 여부 |
| 구조 선택이 필요한가 | 고정 rigid/Coulomb 대 모델 후보 선택 | contact regime·COM·스펀지 rate 변화에서 오차 감소/올바른 기각 |
| 자기 시뮬레이터를 외운 것인가 | 보류 seed/형상/물성 및 Reality 법칙 mismatch | 같은 모델에서만 통하는 성과를 별도 표시 |
| 두 로봇이 실제로 반복하는가 | reset 성공률·소요 시간·zone conflict | pinsetter 접촉 복구와 roller 실행의 연속 기록 |
| 결과를 감사할 수 있는가 | asset → evidence → hypothesis → build → policy → deployment | 숨긴 truth 유출 없이 재생·재실행·실패 원인 확인 |

초기 benchmark 설계안: 3개 사전 고정 조건군 × 10개 보류 episode. 예산 상한은 조건마다 동일하게 두고, 실패·기권도 분모에 포함한다. 보류 데이터는 모델 선택용과 최종 평가용을 분리한다. 이 표본 수는 초기 회귀 gate이며 논문 수준의 우월성 검증에는 사전 power 분석과 확대 평가가 필요하다.

수치 gate는 P0에서 선정한 크기·센서 잡음·제어 한계에 맞춰 실행 전에 task manifest에 고정한다. 예: release 속도 오차, pin pose 오차, tipping 임계각 오차, force NRMSE, 스펀지 압축/복원 오차, contact/solver 예산. 결과를 본 뒤 gate를 완화하지 않는다.

## 7. 2026년 대비 드러낼 기여와 한계

관련 연구는 [조사 §5](../research/simready-bowling-and-model-discovery-2026.md#5-2026년-위치와-선행연구)에 정리했다. 물성 선택, 능동 SysID, VLM 재료 선택 각각은 이미 선행연구가 있다. 조합만으로 독보적이라고 단정하지 않는다.

검증할 기여 가설은 세 가지다.

1. **목표에 충분한 모델을 최소 실험으로 선택**: 파라미터 조정과 모델 부적합을 구분하고 측정 불가능한 값을 정직하게 남긴다.
2. **모델 선택과 행동 검증의 폐루프**: 선택 보고서가 실제 보류 예측·실행 오차와 연결되며, 실패가 다음 실험을 바꾼다.
3. **확장 가능한 증거 계약**: 볼링의 rigid contact와 스펀지의 재료 식별을 같은 Lab/ActionGraph/관측 경계로 구현하고 sim과 hardware의 증거 수준을 구분한다.

공개 산출물은 고정된 asset manifest, 재현 가능한 benchmark 설정, 성공/실패 event log, baseline 비교표, 두 로봇의 인과적 replay다. 대규모 VLA 학습이나 사진 품질 향상은 위 가설을 검증한 다음 우선순위를 판단한다.

## 8. 착수 시 확인할 입력

문서 작성에는 아래 미확정 사항을 가정으로 처리했다. 실제 실행 시 프로파일/연결 검사로 해소한다.

- 보유 로봇의 정확한 Panda/FR3 모델, 대수, 그리퍼·툴·가용 payload, 제어 인터페이스.
- 축소 tabletop 장면 허용 여부. 정규 볼링이 고정 요구면 부적합 판정 뒤 대체 툴·시설을 별도 설계한다.
- wrist/overhead 카메라, 공 회전 관측 표식, 힘/토크·질량 계측 producer의 가용성.
- 실제 핀·공·레인의 재질·치수·오일 조건. 조사한 prior와 현장 계측값의 차이.

CLI 기반 다음의 볼링 구현 작업은 이 입력들을 수집·검사하는 P0이다. 현 단계 문서에는 하드웨어 연결이나 물성 측정이 끝났다는 주장을 넣지 않는다.
