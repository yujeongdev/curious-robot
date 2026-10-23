# SimReady·Franka·볼링과 자율 물리 모델 선택의 근거

> 인접 문서: [개발 계획](../plans/2026-10-24-autonomous-physics-discovery-and-bowling.md) · [프로젝트 설계 결정](../specs/2026-10-24-experiment-driven-twin-construction.md)

2026-10-24 작성. 범위: 외부 에셋, 제조사 한계, 볼링 물리, 2026년 관련 연구.
기준: 아래 공식 문서·원논문과 명시한 Hugging Face revision, 2026-10-24 확인.

## 1. 이번 NVIDIA 공개와 실제 에셋 검색

NVIDIA가 2026-10-21 공개한 SimReady Central은 여러 공급사의 Hugging Face 데이터셋을 연결하는 OpenUSD 에셋 카탈로그다. 공식 발표는 2,000개 이상의 에셋, 자연어 검색, use-case별 profile/validation을 설명한다. 공개 라이브러리라는 사실과 특정 physics backend에서 원하는 물리량이 검증되었다는 사실은 구분해야 한다. [NVIDIA 공식 소개](https://huggingface.co/blog/nvidia/simready-central-assets-robotics-ai-factories), [SimReady Central 문서](https://docs.omniverse.nvidia.com/simready/latest/simready-central.html)

### 확인한 범위

| 항목 | 조사 결과 | 고정 출처 |
| --- | --- | --- |
| NVIDIA 본체 데이터셋 | root pagination 전체 2,576개 항목, asset 폴더 2,575개 확인 | [nvidia/simready-assets](https://huggingface.co/datasets/nvidia/simready-assets/tree/524747d9b5e1f21b5b18b56041788c01d745f1a1) |
| 해당 revision의 볼링 이름 | bowling ball/pin/lane으로 확인되는 폴더 미발견 | revision `524747d9b5e1f21b5b18b56041788c01d745f1a1` |
| 중앙 semantic 검색 | 위 세 query의 상위 5개씩 metadata 확인; 볼링 객체로 검증된 결과 없음 | [중앙 검색](https://nvidia-simready-central.hf.space) |
| 볼링 핀 검색의 오탐 | Palatial `D00672`, `D00980`은 주방 밀대 `Rolling Pin` | [Palatial library](https://huggingface.co/datasets/PalatialSim/palatial-nv-asset-library/tree/69a87329036cec0440403351c12230970c4e67d8) |
| 공 검색의 오탐 | 탁구공·구슬·`D00509 Metal Ball` 등 | 같은 Palatial revision |
| 라이선스 sample | `misc_golfBall_a01`의 파일은 CC BY 4.0 | [에셋별 LICENSE.txt](https://huggingface.co/datasets/nvidia/simready-assets/blob/524747d9b5e1f21b5b18b56041788c01d745f1a1/misc_golfBall_a01/.metadata/LICENSE.txt) |

이는 모든 공급사에 볼링 에셋이 없다는 증명이 아니다. 이름 검색과 제한된 상위 결과 확인의 범위를 가진다. 골프공/밀대를 볼링공/핀으로 잘못 채택해서는 안 된다. 파라메트릭 볼링 형상은 외부 볼링 규격을 근거로 만들 수 있는 대안이다.

검색 결과의 NVIDIA revision은 `653d91e182011f357cf1644013cc94ddf194d349`였으며 위 최신 dataset revision과 달랐다. 결과의 repo/revision/path를 그대로 보존하고 `main`으로 바꿔 해석하지 않아야 한다.

조사 시점 실제 서비스에서는 `POST /search_hybrid`가 동작했고, OpenAPI가 안내하는 `/v3/deepsearch/search` GET은 UI를 반환했다. 이 경로는 안정적 API 계약으로 확인된 것이 아니다. 구현 전 [OpenAPI](https://nvidia-simready-central.hf.space/openapi.json)와 서비스 동작을 재확인해야 한다.

USD에서 단위/축, visual/collision, mass/inertia, 재료와 profile 지원은 각각 검사 대상이다. 저자가 쓴 물성과 배포 현장의 실측 물성은 같다는 보장이 없다. license도 sample의 CC BY를 라이브러리 전체에 일반화하지 않고 에셋별로 확인해야 한다.

## 2. Franka의 한계: 제품과 인터페이스를 구분

| 항목 | FER/Panda | FR3 | 해석 |
| --- | --- | --- | --- |
| FCI Cartesian translational limit | `1.7 m/s` | `3.0 m/s` | 인터페이스 한계이며 모든 자세/부하의 실행 가능 속도가 아님 |
| FCI joint max velocity | 앞 4축 `2.175`, 뒤 3축 `2.61 rad/s` | 앞 4축 `2.62`, 나머지 `5.26/4.18/5.26 rad/s` | FR3는 자세/방향 의존 velocity limit도 확인 |
| FCI translational acceleration | `13 m/s²` | `9 m/s²` | 응용의 운용 가속도 권고가 아님 |
| FR3 datasheet R02212 v2.2.1 | 해당 없음 | TCP 최대 `2 m/s`, payload `3 kg`, reach `855 mm` | FCI의 `3 m/s`와 다른 사양 문맥 |

출처: [현재 Franka robot specifications](https://frankarobotics.github.io/docs/robot_specifications.html), [FR3 제조사 datasheet](https://franka.de/hubfs/Datasheet%20Franka%20Research%203_R02212_2.2.1_EN.pdf?hsLang=en).

현재 FR3 문서는 `Robot::getUpperJointVelocityLimits(q)`와 `getLowerJointVelocityLimits(q)`를 제공한다. 과거 문서와 joint position 범위도 달라 모델·URDF·controller/firmware 버전의 일치가 필요하다. 하나의 TCP 수치를 모든 Franka의 기준으로 쓰기 어렵다. [공식 robot specifications](https://frankarobotics.github.io/docs/robot_specifications.html)

Panda Hand 매뉴얼 사양은 span `80 mm`, 질량 `0.73 kg`, 연속 grasp force `70 N`, 최대 `140 N`이다. 이는 모든 FR3 툴의 사양이 아니다. 물체를 들어 올릴 때는 tool/adapter 질량·관성, grasp geometry와 접촉 마찰도 고려해야 한다. [Franka Emika Robot manual, p.164](https://download.franka.de/documents/100010_Product%20Manual%20Franka%20Emika%20Robot_10.21_EN.pdf)

## 3. 볼링 치수와 마찰을 어떻게 설정할까

### 정규 볼링의 치수

아래 SI 값은 USBC 2026-03 규격의 단위 변환이다. (내 유도)

| 항목 | USBC 값의 SI 표현 |
| --- | --- |
| 공 직경 | `215.90–218.31 mm` |
| 공 최대 질량 | `7.257 kg` (`16 lb`); 최소 질량 제한 없음 |
| 핀 목표 질량 | 약 `1.588 kg`, 허용 약 `1.531–1.644 kg` |
| 핀 목표 높이 | `381 mm` |
| 핀 COM 높이 | 목표 약 `146.84 mm`, 허용 약 `142.88–150.80 mm` |
| foul line → head pin | `18.288 m` |
| 레인 목표 폭 | `1.0541 m` |

출처: [USBC 2026 Equipment Specifications Manual](https://images.bowl.com/bowl/media/assets/usbc/equipment%20specs/26_231-26-march-es-manual.pdf).

정규 공의 직경은 stock Panda Hand span보다 크다. 볼링공을 바닥에서 지지하며 미는 툴과 공을 잡아 들어 나르는 툴은 다른 조건을 요구한다. 작은 장면이 필요할 때는 치수·질량·관성·접촉 특성을 별도로 정의해야 한다. (사양 비교에 따른 추론)

### 사용할 수 있는 문헌 prior와 사용할 수 없는 상한값

Ji et al.의 볼링 모델은 kinetic friction의 대표 근사로 **dry 약 `0.20`, oiled 약 `0.04`**를 제시하며, 레인 상태·오일 두께·공 표면에 따라 달라진다고 명시한다. oil pattern의 수치도 모델 근사다. 이 값은 재현용 시뮬레이션의 **문헌 기반 nominal prior**로 쓸 수 있지만 특정 설치의 실측값은 아니다. [원논문, Table 1 및 §2.2](https://arxiv.org/pdf/2210.06753)

반면 USBC의 non-high-performance ball 분류에는 dry on-lane COF `≤0.125`, ball lab COF 상한 `0.320`, lane surface 검사 상한 `0.290` 등이 있다. 서로 다른 승인 시험의 상한이므로 일반적인 oiled/dry ball–lane μ로 복사할 수 없다. 문헌의 `0.20`과 분류 상한을 비교할 때도 공 분류와 시험법을 맞춰야 한다. [USBC 매뉴얼](https://images.bowl.com/bowl/media/assets/usbc/equipment%20specs/26_231-26-march-es-manual.pdf)

USBC의 SOP-LANE-3은 urethane feet가 있는 sled를 끄는 패널 시험이다. ball–lane 구름·미끄럼 시험과 같지 않다. [SOP-LANE-3, 2026-04-22](https://images.bowl.com/bowl/media/assets/usbc/equipment%20specs/sops/coefficient-of-friction-%28sop-lane-3%29-4-22-26.pdf)

### 실제 마찰 추정에 필요한 관측

USBC SOP-BALL-31은 EARL 로봇, Brunswick ProLane, Kegel Red Square/Fire conditioner와 SPECTO tracking을 사용한다. oiled `10–40 ft`와 dry `41–51 ft` 구간을 나눠 감속과 slip 방향으로 평균 COF를 추정한다. 현대 release에서는 작은 각도 근사의 한계를 주의한다. 이 절차는 마찰을 측정하는 방법에 대한 근거이지, 어느 현장에서나 적용할 고정 μ를 주지 않는다. [SOP-BALL-31, 2025-08-22](https://images.bowl.com/bowl/media/assets/usbc/equipment%20specs/sops/sop-ball-31-ball-on-lane-cof-(web).pdf)

공의 마찰력 방향은 중심 이동 방향과 항상 같지 않다. 접촉점의 `v + ω×r`를 사용해야 하며, 순수 구름에서 감속을 만드는 저항과 sliding friction은 구분된다. [Ji et al., §2.1](https://arxiv.org/pdf/2210.06753)

USBC Ball Motion Study는 surface roughness, dry/on-lane COF, oil absorption과 RG를 별도 변수로 분석한다. 균일 μ만으로 설명되지 않는 잔차가 있다는 근거이며, 실제 oil profile은 회차에 따라 변할 수 있다. [USBC Ball Motion Study](https://images.bowl.com/bowl/media/legacy/internap/bowl/equipandspecs/pdfs/08ballmotionstudy.pdf)

확인하지 못한 값: 이번 설치의 actual oil/dry COF, pin–lane 마찰, 공–핀 contact stiffness/damping, scaled toy 재료의 rolling resistance. 이들은 현장/물체별 측정 대상이다. 정규 볼링 문헌 값을 장난감 재료의 실측값으로 표시하면 안 된다.

## 4. Neo-Hookean, VBD와 엔진 지원 경계

Neo-Hookean은 변형에 따른 에너지/응력을 정의하는 constitutive law다. VBD는 implicit dynamics를 푸는 수치 알고리즘이다. 따라서 “Neo-Hookean과 VBD 중 하나”는 다른 축을 비교하는 표현이다. [Stable Neo-Hookean 원논문](https://www.tkim.graphics/NEO/StableNeoHookean2018.pdf), [VBD 원논문](https://arxiv.org/abs/2403.06321)

Newton의 공식 solver 안내는 articulated rigid contact에 MuJoCo, deformable/rigid의 unified VBD, bulk material에 implicit MPM 등 서로 다른 기능 경계를 제시한다. VBD는 experimental이며 solver에 따라 joint/actuation/force 지원이 다르다. VBD라는 선택만으로 foam의 crush·plasticity·poroelasticity가 구현되지는 않는다. [Newton solver matrix](https://newton-physics.github.io/newton/latest/solvers/index.html), [VBD guide](https://newton-physics.github.io/newton/latest/solvers/vbd.html)

공식 material support 표에서 MuJoCo는 `mu_torsional`/`mu_rolling`을 사용하지만 일반 `restitution` 필드의 지원 solver 목록에는 들어 있지 않다. 따라서 물리 효과가 필요한 경우 field의 존재뿐 아니라 해당 backend가 어떻게 접촉 응답을 계산하는지 확인해야 한다. [Newton contact material support](https://newton-physics.github.io/newton/latest/solvers/index.html#contact-material-support)

MuJoCo contact는 tangential/torsional/rolling friction과 `condim`을 구분한다. geometry 두 개의 coefficient가 어떤 방식으로 합쳐지는지도 설정에 따라 결정된다. friction parameter를 두 shape에 적는 것과 원하는 pair 계수를 얻는 것은 같은 일이 아니다. [MuJoCo contact parameters](https://mujoco.readthedocs.io/en/stable/modeling.html#contact-parameters)

현재 Newton 공식 observables 문서의 VBD `CONTACT_F`는 rigid-rigid 및 rigid-soft particle/edge/face record의 wrench를 제공한다. soft row는 `SensorContact`의 집계 대상이 아니며 soft self-contact는 출력하지 않는다. force는 최종 구성에서의 solver contact law 출력이며 time-step 평균과 다르다. articulated/soft coupling에서는 어느 solver가 해당 force를 소유하는지도 구분해야 한다. [Newton solver observables](https://newton-physics.github.io/newton/latest/concepts/solver_observables.html)

`latest` 문서는 움직이는 참조다. 실제 구현은 고정한 엔진 버전과 constructor/feature test로 확인해야 하며, experimental API와 기본값을 조용히 승계하면 재현성이 깨질 수 있다.

## 5. 2026년 위치와 선행연구

| 자료 | 이미 다루는 것 | 이 자료만으로 입증되지 않는 것 |
| --- | --- | --- |
| [Real2Sim with VLM-generated BTs, 2026-01](https://arxiv.org/html/2601.08454v1) | 목표에서 필요한 물성을 선택하고 atomic BT를 생성해 Panda로 mass/friction/height 측정, MuJoCo 구성 | 모델 family 선택·실험 정보 가치·보류 반증의 전체 문제를 해결했다는 근거는 아님 |
| [PhysWorld, 2025-10](https://arxiv.org/html/2510.21447v1) | VLM-assisted constitutive model 선택, MPM digital twin 물성 최적화, synthetic interaction과 GNN | 실제 로봇의 다음 실험 선택/자원·복구 계약을 제공한다는 근거는 아님 |
| [SPI-Active, CoRL 2025](https://proceedings.mlr.press/v305/sobanbabu25a.html) | sampling-based SysID와 Fisher information 기반 능동 탐색 | tabletop 모델 선택 전반의 우월성을 입증하는 자료는 아님 |
| [Real-is-Sim](https://real-is-sim.github.io/) | 지속 동기화되는 dynamic twin을 거치는 policy 실행·평가 | 관측과 독립된 counterfactual 검증/모델 식별이 자동 해결되지는 않음 |
| [NVIDIA Isaac 발표, 2026-10-21](https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Advances-Isaac-Platform-for-General-Purpose-Robotics-With-New-Reference-Humanoid-Frameworks-Simulation-Pipelines-and-Data/default.aspx) | SimReady, agent-ready Isaac Lab 3.0, Isaac OS runtime, GR00T/Cosmos workflow | 특정 시스템의 측정 정확도·신뢰성·연구 novelty를 보증하지 않음 |

특히 첫 논문은 단일 로봇·정적 workspace와 제공된 object pose, 사전에 구현된 atomic action library를 전제한다. “에이전트가 필요한 물성을 정하고 로봇으로 측정한다”는 설명 자체는 이미 가까운 선행연구다. [Real2Sim §III–IV](https://arxiv.org/html/2601.08454v1)

PhysWorld의 Appendix B.1에는 VLM-assisted constitutive model selection이 명시되어 있다. “LLM이 재료 법칙을 선택한다”만으로 새로운 연구 기여라고 하기 어렵다. [PhysWorld Appendix B](https://arxiv.org/html/2510.21447v1)

위 논문들로부터의 해석: 연구 기여를 주장하려면 실험 예산 대비 task prediction 개선, 모델 부적합 탐지, 다음 행동을 통한 구분 가능성, 다른 재료/조건에서의 transfer를 baseline과 측정해야 한다. 각 요소의 통합은 유용한 engineering 기여지만, 우월성이나 독창성은 실험 전에는 가설이다.

영상 world model이나 VLA는 장면 해석·후보 생성·학습 데이터 확대에 도움을 줄 수 있다. 그 출력이 교정된 force sensor, material posterior, 물리 실행 검증을 대신한다는 근거는 위 자료에서 확인하지 못했다. 역할과 검증 수준을 분리하는 것이 합리적이다. (자료 범위에 따른 추론)
