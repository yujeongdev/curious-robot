# Curo로 task를 선택하고 시뮬레이션·실험을 실행하기

> 인접 문서: [명령 구조와 지원 경계](../specs/2026-10-24-task-loading-and-curo-cli.md) · [개발 계획](../plans/2026-10-24-autonomous-physics-discovery-and-bowling.md)

2026-10-24 작성. 범위: 현재 CLI의 사용법과 agent workflow.
기준: 저장소의 `curo --help`, `curo capabilities --json`; 명령별 지원 상태가 우선한다.

## 1. 환경과 지원 기능 확인

저장소에서 기존 lock된 Python 환경을 준비한다.

```bash
uv sync --extra dev --extra web
uv run curo --help
uv run curo capabilities --json
```

`capabilities`의 명령별 상태를 확인한다. 목록과 입력 정보는 실제 Click command tree에서 생성하며, 등록된 명령에는 실제 구현과 미구현 진입점이 함께 있다. 에이전트는 실행 전에 해당 leaf의 `--help`로 입력을 확인한다. catalog/help 조회에는 GPU 초기화가 필요하지 않다.

미구현 명령은 “아직 구현되지 않았습니다”와 이유/다음 작업을 출력하고 exit code `3`을 반환한다. 이는 실행 성공이 아니며 artifact도 생성하지 않는다. 명령이 존재한다는 사실만으로 볼링·스펀지·실물 로봇까지 지원한다고 판단하지 않는다.

## 2. Task 선택과 화면 load

```bash
uv run curo tasks list --json
uv run curo tasks show push-t-bar-v1 --json
uv run curo web --task push-t-bar-v1
```

`tasks list`에서 사용 가능한 ID를 고른다. cube/T-bar는 현재 등록된 예시이며 task마다 목표·형상·실험·기본 입력이 다르다. `web --task`는 선택한 task를 Agent Lab 화면에 불러오고 실험을 시작하지 않는다. Run 버튼을 누르거나 명시적인 `--autorun`으로 실행한다.

화면에서 task를 변경하면 해당 task의 기본 material 입력으로 전환한다. unknown task와 catalog 조회 실패는 실행을 막으며 다른 task로 대체하지 않는다.

`tasks load TASK`는 향후 공통 task/session service용 진입점이다. 해당 명령이 미구현인 현재는 화면 load에 `web --task`, headless 실행에는 명시적인 `--task`를 사용한다. 이 차이를 전역 active task가 저장된 것처럼 설명하지 않는다.

## 3. 실제 world의 최소 셋업

```bash
uv run curo sim setup --task push-cube-v1 --device cuda:0 --output artifacts/curo-setup-cube --json
```

현재 setup은 선택한 task의 공개 prior로 world를 만들고 제한된 stepping과 observation을 확인한다. 출력 디렉터리는 새 경로를 사용한다. 구체적인 step 수나 reduced scene 옵션은 `curo sim setup --help`에서 확인한다.

보고서의 task/preset/geometry identity, requested/resolved/actual device, runtime version, 초기화·finite-state·completed-steps·observation 체크를 읽는다. 요청한 device alias와 실제 device가 다르면 실패하며 다른 backend로 전환하지 않는다. state는 매 step 검사하고 observation은 별도로 검사하므로, `finite_state=true`만으로 최종 관측도 성공했다고 판단하지 않는다. 초기화나 실행이 실패하면 성공한 셋업으로 사용하지 않는다. 기존 `setup_report.json`은 덮어쓰지 않는다.

이 결과는 **prior-only setup smoke**다. 물성 식별·접촉 정확성·정책 실행·하드웨어 준비를 통과했다는 뜻은 아니다. private Reality 물성이나 hidden seed를 읽어 채운 것도 아니다. 완전한 자동 에셋·재료·solver 구성은 adapter별 구현과 검증을 거쳐 확장된다.

## 4. 전체 discovery 실행

```bash
uv run curo run --task push-cube-v1 --pipeline isolated --device cuda:0 --output artifacts/curo-discovery-cube --json
```

이 명령은 현재 지원되는 isolated Reality→Twin→Reality pipeline을 실행한다. 비어 있지 않거나 다른 실행이 선점한 출력 경로는 거절한다. task 실패는 종료 코드와 결과에 그대로 남기며 출력 디렉터리와 생성된 실패 manifest를 보존한다. 로그는 stderr, `--json` 실행 결과는 stdout으로 구분하므로 에이전트는 JSON의 status·manifest·task/pipeline identity와 실제 평가 결과를 확인한다. 입력 파싱 오류는 stderr와 exit 2를 사용하므로 JSON 결과의 존재도 확인한다.

기존 runner가 필요한 경우 `--pipeline legacy`를 명시한다. 현재 UI와 기존 `curious-robot run`은 legacy 경로이며 신규 CLI 기본값과 다르다. pipeline 통합 전에는 양쪽 결과를 동일 실행 의미로 비교하지 않는다.

`sim setup` 결과를 읽은 뒤 run을 실행할 수 있지만, 현재 `run --setup`으로 해당 world를 재개하는 기능은 제공하지 않는다. 각 명령은 자기 scope의 world를 생성한다. 단순 setup report를 calibrated model이나 실행 승인으로 넘기지 않는다.

## 5. 단계별 명령을 사용하는 법

전체 pipeline과 별도로 에셋·실험·SysID·모델·정책·실행 관리·결과·실물 명령군이 노출되어 있다. `capabilities`에서 미구현 상태인 leaf는 실제 service가 연결되기 전까지 작업을 수행하지 않는다.

예를 들어 다음은 SysID의 독립 실행 인터페이스를 확인하는 명령이다.

```bash
uv run curo sysid fit --help
```

`capabilities`가 해당 operation을 미구현이라고 하면 dataset을 생성하거나 추정이 끝났다고 보고하지 않는다. hint가 가리키는 evidence/estimator service를 먼저 구현해야 한다. 기존 전체 pipeline 내부의 SysID와 standalone CLI operation의 구현 상태는 다를 수 있다.

| 목적 | 명령군 |
| --- | --- |
| 에셋 탐색/확인 | `assets search`, `assets inspect` |
| 셋업 검증/복구 | `sim validate`, `sim reset` |
| 필요한 실험 탐색/실행 | `experiments list`, `experiments run` |
| 물성 추정 | `sysid fit` |
| 모델 후보/선택 | `models list`, `models select` |
| 행동 계획/검증 | `policy plan`, `policy validate` |
| 실행 조회/이벤트/중단 | `runs status`, `runs events`, `runs stop` |
| artifact/재생 | `artifacts inspect`, `replay` |
| 진단/실물 | `doctor`, `hardware preflight`, `hardware deploy` |

## 6. 에이전트에게 줄 요청

다음처럼 목표와 실행 범위를 전달하면 된다.

> `$curo`로 현재 task와 지원 기능을 확인하고, T-bar task를 화면에 로드해줘. 실험은 시작하지 마.

> `$curo`로 cube task의 GPU 시뮬레이션 셋업을 실행하고, 실제로 통과한 체크와 아직 검증하지 않은 물리를 정리해줘.

> `$curo`로 지원되는 task의 isolated discovery를 실행해줘. 실패하면 보고서와 증거를 확인해서 원인을 설명하고 미구현 기능은 구분해줘.

repository skill은 [.agents/skills/curo/SKILL.md](../../.agents/skills/curo/SKILL.md)에 있다. CLI/service가 command 계약을 소유하고, 스킬은 적절한 task·operation 선택과 결과 해석을 안내한다. 실행·복구·하드웨어의 권한이나 검증을 스킬 문장만으로 우회하지 않는다.
