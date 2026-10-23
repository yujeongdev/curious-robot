# Task를 로드하고 전체 파이프라인을 호출하는 curo CLI

> 인접 문서: [전체 구현 계획](../plans/2026-10-24-autonomous-physics-discovery-and-bowling.md) · [시뮬레이션과 발견 계약](2026-10-24-experiment-driven-twin-construction.md) · [에이전트 사용 가이드](../guides/curo-agent-workflow.md)

2026-10-24 작성. 범위: task 선택·로드, 모듈별 CLI, 미구현 명령의 명시적 계약, 에이전트 스킬.
기준: `e56d6b3b95b2fb037be664027c62bcabc530edfe` 위 작업 트리; 실제 지원 상태는 `curo capabilities --json`으로 확인.

## 1. 제품의 단위는 선택한 Task다

cube와 T-bar는 여러 task의 예시다. 시스템과 화면의 이름·실행 경로를 이 두 객체로 나누지 않는다. `push-cube-v1`, `push-t-bar-v1`, 향후 bowling/sponge 등 등록된 task를 선택하면 목표·씬·관측·실험·평가 계약을 로드한다.

object와 task는 다르다. 같은 핀으로 밀기·전도 임계각 측정·볼링을 할 수 있고, 하나의 볼링 task에는 공·핀·레인과 두 로봇이 포함된다. TaskDefinition은 목표/규칙, ObjectArchetype은 형상, TaskPreset은 실행할 조합이다. 현재 단일 물체·PlanarGoal preset은 기존 task용 adapter로 유지한다. 여러 물체나 스펀지 평가를 PlanarGoal의 선택적 필드에 계속 욱여넣지 않는다.

목표 구조는 다음과 같다.

```text
task catalog
  → selected task ID + immutable definition/preset version
  → resolved task: input schema, scene binding, required observations,
                   experiment/model/action families, reset, goal evaluator
  → simulation setup or hardware readiness
  → discovery and execution run
  → evidence, result and replay
```

catalog는 JSON 데이터와 allowlisted Python builder/validator를 연결한다. task 파일의 문자열을 임의의 모듈 import나 shell 명령으로 실행하지 않는다. task 추가는 catalog와 필요한 adapter 등록으로 끝내고 CLI/UI에 task 이름 조건문을 추가하지 않는다.

## 2. Load, setup, run을 구분한다

| 동작 | 결과 | 시작하지 않는 작업 |
| --- | --- | --- |
| 선택·조회 | task 정의·입력·요구 capability를 확인 | world 생성, 로봇 동작 |
| 화면에 load | 선택한 task와 기본 입력을 화면에 표시 | Run 생성, 자동 실험 |
| sim setup | recipe→실제 world→bounded smoke 결과 | 물성 식별 성공이나 정책 실행을 암시하지 않음 |
| run | 명시한 task/pipeline으로 실험·추정·검증·실행 | 다른 task/backend로 자동 전환 |

현재 화면 load는 `curo web --task TASK`와 task 선택기로 수행한다. `--autorun`을 명시했을 때만 자동 실행한다. unknown task는 오류이며 cube/T-bar로 조용히 대체하지 않는다. task를 바꾸면 task별 기본 입력을 다시 로드하고 이전 task의 material override·goal을 가져오지 않는다.

현재 `tasks/description.py`의 immutable `ResolvedTask`가 preset·definition·archetype을 한 번에 해석하고 공통 summary/document를 제공한다. CLI catalog, HTTP task 조회, setup service는 이 binding을 재사용한다. UI는 CLI subprocess를 실행하지 않고 Python service를 호출한다. setup/run까지 CLI·HTTP가 같은 application service로 통합하는 일은 다음 단계다. active task를 암묵적인 전역 변수로 관리하지 않고 request/run마다 task ID·버전을 기록한다.

현재 기반에는 의미가 다른 두 실행 경로가 있다. 기존 `curious-robot run`과 UI RunManager는 legacy runner, `lab-friction`은 isolated Reality/Twin/Evaluator다. 신규 `curo run`은 `--pipeline isolated|legacy`를 명시적으로 제공하고 기본값은 `isolated`다. 결과에 pipeline을 기록한다. UI까지 같은 service로 통합하는 작업은 별도 migration이며, 이번 façade를 동일 실행 의미의 완성으로 표시하지 않는다.

## 3. 명령 트리

아래는 지금 노출할 명령 인터페이스다. 구현이 없는 명령도 help/capability 목록에 나타나지만 실제 작업을 실행하지 않는다. 구체적인 flag는 해당 명령의 `--help`가 현재 계약이다.

| 단계 | 명령 | 이번 기반의 동작 |
| --- | --- | --- |
| 탐색 | `curo capabilities --json` | command별 구현 상태·제한 확인 |
| task | `curo tasks list`, `curo tasks show TASK` | 실제 catalog 조회 |
| task session | `curo tasks load TASK` | 공통 session service 전까지 `not_implemented`; 화면 load는 `web --task` 사용 |
| 화면 | `curo web --task TASK` | task를 선택해서 로드, 실행은 별도 |
| 에셋 | `curo assets search QUERY`, `curo assets inspect ASSET` | 미구현 진입점 |
| 시뮬레이션 | `curo sim setup --task TASK` | 기존 지원 task의 prior world 빌드·bounded stepping |
| 검증·복구 | `curo sim validate --setup PATH`, `curo sim reset --setup PATH` | 미구현 진입점 |
| 실험 | `curo experiments list --task TASK`, `curo experiments run EXPERIMENT --task TASK` | 미구현 진입점 |
| SysID | `curo sysid fit --task TASK --dataset PATH` | 미구현 진입점 |
| 모델 | `curo models list --task TASK`, `curo models select --task TASK --dataset PATH` | 미구현 진입점 |
| 정책 | `curo policy plan --task TASK --setup PATH`, `curo policy validate --policy PATH` | 미구현 진입점 |
| 전체 실행 | `curo run --task TASK --pipeline isolated` | 기존 isolated pipeline 실행 |
| 실행 관리 | `curo runs status RUN_ID`, `curo runs events RUN_ID`, `curo runs stop RUN_ID` | 통합 run service 연결 전 미구현 |
| 결과 | `curo artifacts inspect ARTIFACT`, `curo replay --run PATH` | canonical façade 미구현; 기존 replay 기능과 구분 |
| 진단 | `curo doctor` | 진단 항목 연결 전 미구현 |
| 실물 | `curo hardware preflight --bundle PATH`, `curo hardware deploy --bundle PATH` | 실물 transport와 admission 연결 전 미구현 |

capability 목록은 실제 Click command tree에서 생성한다. `implemented`는 해당 `Operation`에 callback이 존재한다는 뜻이며 별도 status 필드를 함께 관리하지 않는다. 모든 task/backend에서 성공하거나 물리 capability가 검증되었다는 뜻은 아니다. 실제 지원은 task 요구사항, device, model/producer, admission 결과를 함께 확인한다.

standalone 실험/SysID/model 명령이 미구현이어도 기존 전체 isolated pipeline 내부의 해당 연산이 사라지는 것은 아니다. CLI operation별 증거·입출력 계약이 준비될 때 service에 연결한다. 불완전한 중간 파일을 만들어 pipeline이 실행된 것처럼 표시하지 않는다.

## 4. CLI 코드를 나누는 기준

```text
curious_robot/
  curo_cli/
    __init__.py           # entrypoint와 Click 입력 오류·종료 처리
    __main__.py           # python -m 진입점
    app.py                # native Click group/command 조합
    core.py               # Operation 경계, CommandFailure와 Report
    parameters.py         # 재사용하는 Click option 생성 함수
    registry.py           # 실제 Click tree에서 capability 문서 생성
    output.py             # human/JSON 출력, stderr 진단, 출력 경로
    commands/
      tasks.py            # catalog 조회와 capability 조회
      simulation.py       # setup service의 얇은 adapter
      runs.py             # isolated/legacy pipeline adapter
      web.py              # 기존 Agent Lab server adapter
      pending.py          # callback이 없는 native command와 연결 hint
  sim_setup.py            # 설정·보고서·smoke 검사 helper와 setup service
  tasks/
    description.py        # 공유 ResolvedTask binding과 public 문서
    registry.py           # catalog/definition/preset 조회
  lab/                    # 실험·증거·Twin·실행의 authority 경계
  cli.py                  # 기존 curious-robot 호환 entrypoint
```

핵심은 입력 파싱·공통 실행 경계·domain 실행의 변경 이유를 분리하는 것이다. Click의 group, command, option, argument 자체가 유일한 명령 정의다. `registry.py`는 이 tree를 읽는 discovery 모듈이며 별도의 descriptor schema나 parser를 소유하지 않는다. typed callback을 직접 연결하고 무거운 domain import는 실행 함수 안으로 미룬다. 순수 catalog/help 조회에서 Newton·Warp·웹 서버를 import하거나 GPU를 초기화하지 않는다. 이 조합과 import 방식은 [Click의 complex application 안내](https://click.palletsprojects.com/en/stable/complex/)를 따른다.

| 기존 설계 | 현재 구조 | 변경 이유 |
| --- | --- | --- |
| descriptor와 argparse tree를 별도로 구성 | native Click tree에서 help·입력·capability 생성 | 명령 정의의 중복과 상태 불일치를 없앤다 |
| 문자열 handler 경로와 동적 dispatch | typed callback을 직접 연결 | 함수 의존성을 명시하고 typecheck가 callback을 검사한다 |
| command마다 출력·실패 처리 | `Operation(click.Command)`과 `CommandFailure` | JSON·진단·종료 코드와 실패 증거 경로를 한 경계에서 처리한다 |
| CLI·HTTP·setup이 task 해석을 반복 | immutable `ResolvedTask` 재사용 | task identity와 public 문서가 같은 catalog binding을 사용한다 |

`Operation`은 callback 반환값의 출력, 미구현 상태, 진단의 stderr 분리, 오류의 종료 코드를 맡는다. `CommandFailure`는 실패 원인과 출력 디렉터리·manifest 등의 context를 명시적으로 전달한다. command들은 공통 출력·option helper를 사용하며 서로의 command module에서 helper를 가져오지 않는다.

현재 application service는 함수로 충분하고, setup의 교체 지점도 typed callable `WorldFactory` 하나다. generic ABC나 service/class hierarchy를 추가할 서로 다른 구현 계약이 아직 없다. 실제로 여러 구현에서 같은 호출·수명주기 계약을 공유해야 할 때 필요한 interface를 도입한다. 기존 함수를 얇은 adapter로 연결하고 물리 로직을 CLI 안에 복제하지 않는다.

미구현 operation을 채우는 절차는 다음과 같다.

1. CLI 밖에 domain service를 구현하고, 해당 command module에 typed callback을 `cls=Operation`으로 등록한다. 입력은 native Click option/argument로 정의한다.
2. `commands/pending.py`의 같은 이름 등록을 제거한다. 예를 들어 `sim validate`는 `simulation.py`의 `sim` group에 등록하며, 나중에 호출되는 `register_pending()`이 실제 callback을 덮어쓰지 않도록 기존 pending 항목을 제거한다. 새 최상위 group이면 `app.py`에서 조합한다.
3. command help·입력 거절·JSON/실패 증거와 domain 동작을 검증한다. `capabilities --json`이 같은 tree의 callback에서 구현 상태를 자동으로 읽는지 확인한다. 별도 registry status나 문자열 handler 경로는 수정하지 않는다.

기존 명령 이름은 유지해 script를 깨지 않는다. 새 가이드·스킬은 `curo`를 기본으로 사용하며, 기존 명령이 필요한 경우 의미 차이를 명시한다. 향후 deprecation은 사용처 migration과 별도 검증을 거친다.

## 5. 아직 없는 기능은 사실대로 실패한다

미구현 command를 호출하면 사람이 읽는 출력에는 “아직 구현되지 않았습니다”와 operation별 다음 작업을 표시한다. JSON 출력에는 `status=not_implemented`, stable error code와 hint를 담고 **exit code 3**을 반환한다. 결과 artifact나 성공 run을 만들지 않는다.

공통 종료 코드의 의미는 다음과 같다.

| 코드 | 의미 |
| --- | --- |
| `0` | 요청한 command 완료; 물리 task 성공 여부는 결과 필드를 별도 확인 |
| `1` | runtime/예상 밖 실행 오류 |
| `2` | 입력·검증 실패 또는 실행한 task/pipeline의 실패 |
| `3` | 명령은 존재하나 구현되지 않음 |

입력 파싱을 통과한 leaf의 `--json`은 stdout에 하나의 versioned JSON 결과만 내보낸다. progress·진단·solver 로그는 stderr로 분리한다. spawned worker와 native library의 stdout도 결과 JSON을 오염시키면 안 된다. 필수 입력 누락·잘못된 option 같은 Click 입력 오류는 stderr와 exit 2를 사용한다. JSON 소비자는 먼저 종료 코드와 결과 유무를 확인하고, 실행 결과에서는 문자열 문구 대신 status/code와 typed data를 읽는다.

필수 입력 누락은 Click 입력 오류이며 unknown task는 validation 오류다. unsupported task를 기존 task로 바꾸거나, hardware 요구를 sim으로 바꾸거나, `sim setup` 대신 전체 실험을 실행하는 fallback은 제공하지 않는다.

신규 `curo run`은 비어 있지 않은 출력 디렉터리를 실행 전에 거절하고 `.curo-run.json`을 exclusive creation으로 선점해 동시 실행이 같은 경로를 차지하지 못하게 한다. 이전 manifest·보고서·replay를 보호하며, 선점/실행 실패 JSON에는 자동 생성한 출력 경로도 남긴다. 저장된 failure manifest가 있으면 그 경로를 함께 제공한다. 품질 gate의 `CapabilityExecutionError`는 `validation_failed`와 exit 2, 예상 밖 runtime 오류는 exit 1로 구분한다.

## 6. 이번 sim setup의 정직한 범위

실행 중 timeout·연결 오류·손상된 manifest·JSON 직렬화 오류는 예외의 부모 클래스와 무관하게 runtime error/exit 1이다. 입력 해석·명시적 preflight 거절은 exit 2다. `CommandFailure`가 이 경계를 명시하며, encoding 실패도 단일 JSON과 안전한 증거 경로를 반환한다.

최소 setup service는 선택한 현재 task의 public geometry와 명시된 prior로 BeliefEnvironment를 생성하고 제한된 step을 실행한다. task/preset/geometry/runtime/device와 관측 가능한 결과를 보고한다. private Reality parameter를 읽거나 평가 seed로 정답을 재구성하지 않는다.

setup report는 `prior-only`/`smoke`임을 표시한다. 실제 관측으로 calibration한 learned Twin, contact law의 실험적 타당성, 로봇 deploy 승인과 구분한다. 셋업 artifact의 존재만으로 후속 모델 검증을 통과시키지 않는다. 아직 `run --setup`으로 임의 artifact를 실행하는 인터페이스는 제공하지 않는다.

setup service는 world 생성 전에 `setup_report.json`을 exclusive creation으로 확보한다. 기존 보고서와 경쟁 writer를 거절하고, world 실행·cleanup 실패는 같은 보고서에 남긴다. requested device alias를 해석한 값과 실제 world device가 정확히 일치해야 한다. 매 step의 body/joint state를 검사해 도중의 NaN/Inf를 놓치지 않으며, finite-state와 최종 observation 검사는 독립적으로 기록한다. 유한한 stepping 뒤 관측이 실패해도 `finite_state=true`, `observation_returned=false`가 원인을 구분한다.

전체 자동 셋업(에셋 탐색·변환, 여러 로봇, material/solver 선택, 실패 복구)은 [P0a 이후 계획](../plans/2026-10-24-autonomous-physics-discovery-and-bowling.md#p0a--실행-가능한-시뮬레이션-셋업-자동화)대로 지원 adapter와 증거를 추가한다. CPU/GPU backend의 지원 차이는 실제 검사 결과로 남기고 조용히 우회하지 않는다.

## 7. 에이전트가 능동적으로 CLI를 쓰게 하는 계약

셋업은 runtime 생성 전에 `setup_incomplete` 실패 보고서를 기록하고 flush한다. 실행 중 중단되면 읽을 수 있는 기록이 남으며, 정상 종료 결과는 임시 파일로 완성한 뒤 원자적으로 교체한다. world 생성/producer 실행 실패는 `runtime_error`, 명시적 device/유한성 검증 거절은 `validation_failed`로 구분한다.

에이전트는 먼저 `curo capabilities --json`과 task catalog를 읽는다. 사용자가 요청한 목표에 맞는 task를 고르고 definition/goal/예산을 확인한 뒤 setup·run을 실행한다. cube/T-bar를 고정된 기본 연구 시나리오로 취급하지 않는다.

전체 실행 안에서 계획·실험·추정을 매 단계 자연어로 다시 작성하는 대신 실제 CLI/service의 typed 결과와 artifact ID를 다음 입력으로 넘긴다. 구현되지 않은 단계를 만나면 다음 명령을 추측하지 않고 gap과 의존 구현을 설명한다. `--help`와 capability manifest를 실행 가능한 계약으로 사용하고 스킬이 복제된 명령 registry가 되게 하지 않는다.

repository의 `.agents/skills/curo/SKILL.md`가 이 workflow를 안내한다. 필요한 상세 사용법은 guide로 분리하고, 스킬은 task 선택·지원 확인·증거 수준·실패 처리의 핵심만 유지한다.

## 8. 검증과 다음 연결 순서

이번 기반에서 검증할 동작은 다음과 같다.

- help/catalog/capability 조회는 GPU 없이 동작한다.
- 서로 다른 두 task를 선택하면 해당 preset/goal/geometry가 해석된다. unknown ID는 거절한다.
- 화면의 task load는 run을 생성하지 않고, 명시적 autorun만 한 번 실행한다.
- pending command는 exit 3과 `not_implemented`를 반환하며 artifact/robot 동작이 없다.
- 실제 setup의 출력은 JSON으로 읽을 수 있고, prior-only world의 stepping evidence를 가진다.
- 기존 CLI·task/API·replay 계약을 유지한다.

다음 구현은 shared task/session service, setup artifact의 검증된 재사용, run status/events/stop 연결, standalone 실험·SysID·모델 service 순서다. 단계별 callback을 실제 service에 연결하면 capability 상태도 같은 tree에서 바뀐다. 모든 명령의 존재를 전체 파이프라인의 구현 완료로 세지 않는다.

## 9. 이번 구현의 검증 결과

2026-10-24 확인. Click command tree에는 26개 leaf가 있으며 6개는 실제 callback, 20개는 명시적 미구현 진입점이다. portable Python suite 311개, 프런트엔드 unit test 34개를 통과했다. 새 CLI·setup·task description의 측정 line coverage는 90.4%이며 strict mypy는 15개 source에서 통과했다. lint/typecheck/build와 packaged web asset 일치, 스킬 validator도 확인했다. E2E는 다른 작업의 서버를 건드리지 않도록 별도 임시 포트에서 실행한다.

실제 `cuda:0`/Newton `1.7.0`에서 full-robot cube, full-robot T-bar, reduced cube의 prior world를 각각 생성하고 24 step·finite state·pose observation을 확인했다. 이는 setup smoke의 증거이며 물성 식별의 검증은 아니다.

독립 구조 검토에서 발견한 report/run 경로의 동시 쓰기, 정확한 device 확인, 상태/관측 check 분리, 웹 legacy 종료의 JSON 누락, buffered C stdout 혼입을 수정하고 회귀 테스트를 추가했다. 새 ABC나 범용 plugin framework 없이 Click callback·명시적 오류 context·world factory callable을 확장 경계로 유지한다.

새 `curo run`을 통한 cube isolated 통합 실행은 기존 probe의 `excessive_yaw_motion`으로 trajectory dataset이 거절됐다. 실패 manifest와 agent-visible validation artifact를 보존했으며 물리 gate는 변경하지 않았다. CLI의 실패 위치 보존·결과 덮어쓰기 방지·typed validation 오류 처리는 별도 회귀 테스트를 통과했다. 이 통합 실행을 task 성공으로 표시하지 않는다.
