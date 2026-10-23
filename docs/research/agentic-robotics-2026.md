# Agentic robotics orchestration in 2026

> Adjacent documents: [ActionGraph and field deployment](../specs/2026-10-23-action-graph-field-deployment.md)

2026-10-23 작성. 범위: 외부 로봇 orchestration·agent tool·실시간 제어 동향.
기준: 아래 링크의 공식 문서, 2026-10-23 확인.

## 1. 실행 경계

현재의 실용적인 패턴은 LLM이 servo 명령을 생성하는 구조가 아니라, 모델이 typed
skill 또는 graph를 선택하고 deterministic leaf executor가 실행하는 계층형 구조다.
BehaviorTree.CPP 4.8은 reactive condition, 중단 가능한 asynchronous action, typed port,
재사용 subtree를 중심으로 설명한다.
([BehaviorTree.CPP](https://www.behaviortree.dev/docs/tutorial-basics/tutorial_04_sequence/))

Nav2의 production behavior tree는 지속 재계획, contextual recovery, system recovery를
명시적으로 분리한다. 이는 물리 행동 그래프에서도 정상 경로와 bounded recovery를
구분해야 한다는 근거가 된다.
([Nav2 behavior trees](https://docs.nav2.org/rolling/getting_started/nav2_behavior_trees/))

## 2. Lifecycle과 tool contract

ROS 2 managed lifecycle은 configure, activate, deactivate, error processing을 분리해
hardware resource가 준비 이전에 암묵적으로 활성화되지 않게 한다.
([ROS 2 managed nodes](https://design.ros2.org/articles/node_lifecycle.html))

MCP 2025-06 tool contract는 typed input/output schema와 명시적 tool boundary를
일반화했지만, 물리 안전이나 실시간 실행 semantics를 제공하지는 않는다. 로봇에서는
agent-facing tool schema와 safety-critical executor graph를 별도로 유지해야 한다.
([MCP tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools))

## 3. Learned policy의 위치

NVIDIA Isaac GR00T 같은 VLA 계열은 multimodal observation에서 action chunk를 제안하는
연구 경로로 중요하다. 그러나 초기 현장 배포에서는 graph/subgraph 제안, 접촉점 순위,
simulation exploration에 한정하고 deterministic preflight와 executor를 통과시켜야 한다.
([NVIDIA Isaac GR00T](https://developer.nvidia.com/isaac/gr00t))

## 4. Franka와 wrist vision

Franka FCI는 joint/Cartesian position·velocity와 torque 명령, 1 kHz state/control loop를
제공한다. 따라서 MoveJ 의미의 free-space transition, Cartesian pose/velocity 접근,
impedance contact leaf를 서로 다른 atomic skill로 구성하는 것이 자연스럽다.
([Franka FCI](https://frankarobotics.github.io/docs/overview.html))

근접 wrist RGB-D 후보인 RealSense D405는 global shutter, 약 7–50 cm 권장 거리,
42×42×23 mm, 약 60 g 사양이다. 카메라는 1 kHz loop가 아니라 30–60 Hz 외부
visual-servo observation producer로 사용해야 한다.
([RealSense D405](https://www.realsenseai.com/products/stereo-depth-camera-d405/))
