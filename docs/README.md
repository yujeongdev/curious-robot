# Curious Robot documentation map

2026-10-21 작성. 이 저장소의 문서는 다음 갈래로 관리한다.

| 갈래 | 위치 | 역할 |
| --- | --- | --- |
| 아키텍처·설명 | `architecture.md`, `reproducibility.md` | 현재 시스템의 지속되는 설명 |
| 요구사항·감사 | `agent-lab-goal-audit.md` | 목표와 실행 증거 매핑 |
| 회차 설계 | `specs/` | 우리 코드와 경계에 대한 날짜 고정 설계 (`self-building-lab-spine` 포함) |
| UI 설계 | `designs/` | Agent Lab 제품·시각 계약 |
| 실행 증거 | `evidence/`, `media/` | 수치 보고서와 재생 가능한 결과물 |

외부 Newton/Franka 출처와 우리 구현 결론은 같은 문서에 복제하지 않는다. 외부
capability reference는 `capabilities.json`에 pin하고, 그로부터 선택한 우리 구조는
`specs/`와 `architecture.md`에 기록한다.
