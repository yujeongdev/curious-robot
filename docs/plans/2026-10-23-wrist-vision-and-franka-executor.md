# Wrist vision and native Franka executor plan

> 인접 문서: [ActionGraph spec](../specs/2026-10-23-action-graph-field-deployment.md) · [Validation](../validation.md)

2026-10-23 작성. 범위: wrist RGB-D부터 실제 Franka dry-run까지의 PR 단위 실행 계획.
기준: `v0.2.3` 이후 action-graph vertical slice.

## 1. PR sequence

1. Camera contracts
   - intrinsics, RGB/depth encoding, frame artifact, `EE_T_camera`, paired robot-state timestamp, calibration hash
   - RGB/depth bytes는 artifact store, 전체 frame metadata는 content-addressed observation ID로 저장
2. Simulated wrist sensor
   - private Newton state → offscreen RGB/depth producer → estimator
   - estimator가 body pose 배열을 직접 받지 못하도록 boundary test
3. T-bar registration and fusion
   - table removal, public T footprint registration, pose/covariance
   - external camera와 wrist estimate의 residual gate
4. ActionGraph vision nodes
   - capture → validate → estimate → fuse → quality guard → reacquire/replan
5. Native libfranka daemon
   - MoveJ ready, Cartesian pose/velocity, impedance push leaf
   - 1 kHz loop, wired FCI, isolated CPU, watchdog와 collision stop
6. Shadow and dry-run
   - no-motion shadow, free-space dry-run, foam cube, bounded straight push
7. T-bar hardware admission
   - geometry-aware applicability와 held-out acceptance 이후에만 허용

## 2. Acceptance gates

- camera/robot timestamp skew가 설정 한계 이내
- stale, future, wrong-frame, corrupt artifact는 fail-closed
- camera frame에는 hidden mass/friction/ground-truth pose가 없음
- hand-eye, TCP, payload/inertia, table calibration ID가 bundle과 일치
- physical node는 watchdog, force, speed, acceleration, orientation limit를 attest
- contact 중 vision occlusion은 마지막 유효 pose를 갱신하지 않음
- retreat 후 wrist pose를 재획득하고 registered-corner error를 계산
- T-bar 실물 배포는 cube straight-push evidence를 선행 조건으로 함

## 3. Deliberate exclusions

- LLM/VLM을 1 kHz loop에 넣지 않음
- R3F frame을 perception 입력으로 사용하지 않음
- 초기 현장 버전에 RL torque policy를 넣지 않음
- concrete hardware evidence 전에는 real-world success를 주장하지 않음
