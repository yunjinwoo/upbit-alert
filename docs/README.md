# 📚 docs 색인

이 폴더의 문서를 주제별로 분류한 목록. 각 문서는 "무엇을/왜"를 남긴 설계·구현·학습 기록이며,
코드가 이미 담고 있는 내용(구조, 과거 수정 이력)은 여기서 다루지 않는다.

---

## 1. 자동매매 (Auto-trade)

`/auto-trade`(업비트) · `/toss-trade`(토스증권) 페이지의 매매 엔진. 진행 순서대로:

| 문서 | 상태 | 요약 |
|---|---|---|
| [auto-trade-upbit-paper.md](auto-trade-upbit-paper.md) | ⏸️ 중단 (2026-08-21) | 1단계 — 업비트 모의매매(Dry-run) 엔진. 실거래가 자리잡으며 화면에서 제거 |
| [auto-trade-upbit-live.md](auto-trade-upbit-live.md) | ✅ 운영 중 | 업비트 실거래 — 화면(DB)에서 매매 대상/실행 on-off 제어, 2단계 승인 + 안전장치 |
| [auto-trade-toss-paper.md](auto-trade-toss-paper.md) | ⏸️ 중단 (2026-08-22 실거래 전환) | 2단계 — 토스증권(국내주식) 모의매매 엔진. 코드는 남아있지만 배포 목록에서 제외 |
| [auto-trade-toss-live.md](auto-trade-toss-live.md) | ✅ 운영 중 (2026-08-22~) | 토스증권 실거래 — 업비트 실거래와 동일 구조, 정수 주 단위·체결 폴링만 다름 |
| [auto-trade-recovery-dca.md](auto-trade-recovery-dca.md) | ✅ 구현 완료 · 기본 꺼짐 | 회복형 분할 물타기 — 깊은 하락에서 소액 분할매수 → 소폭 반등 익절 반복 + 하드캡(투입 상한 · 소액 손절 · 시간 하드스톱) |
| [auto-trade-downside-watch.md](auto-trade-downside-watch.md) | ✅ Phase 1 머지됨 (PR #62, 2026-09-03) · Phase 2 미정 | 하락위험 코인 관심목록 — 매수 파이프라인의 거울상, 표시 전용 |
| [auto-trade-performance.md](auto-trade-performance.md) | ✅ 머지됨 (PR #71, 2026-09-18) | 매매 성과 화면 — 매수→매도를 한 사이클로 묶어 승률·누적 손익·진입 신호별 기여도 조회 (읽기 전용) |

| [backtest.md](backtest.md) | ✅ 구현 완료 (2026-09-21) | 백테스트 — 당일 상승률/거래대금 상위 종목에 기존 진입·청산 로직을 과거 캔들로 돌려 성과 비교 (수집은 서버 전용) |

관련 게이지 기능(종목당 투입원금 상한 표시)은 `auto-trade-recovery-dca.md`의 1단계로 이미 머지됨(PR #59).

---

## 2. Signal Score (급등 탐지 → 신호 등급화)

급등 탐지기를 신호 등급화(A/B/C) 시스템으로 확장하는 작업. 난이도 낮은 순으로 단계별 진행.

| 문서 | 요약 |
|---|---|
| [signal-score/progress.md](signal-score/progress.md) | **진행 상황 추적용 메인 문서** — 각 단계 상태/날짜/커밋 기록 |
| [signal-score/step1-volume-ratio.md](signal-score/step1-volume-ratio.md) | 20일 평균 거래량 대비 당일 거래량 배수 |
| [signal-score/step2-momentum-score.md](signal-score/step2-momentum-score.md) | 모멘텀 점수 (거래량 배수 + 등락률) |
| [signal-score/step3-supply-demand-score.md](signal-score/step3-supply-demand-score.md) | 수급 점수 (외국인/기관 N일 누적 순매수) |
| [signal-score/step4-rank-stability-score.md](signal-score/step4-rank-stability-score.md) | 시총/랭킹 안정성 점수 |
| [signal-score/step5-market-environment-score.md](signal-score/step5-market-environment-score.md) | 시장/업종 환경 점수 |
| [signal-score/step6-risk-penalty.md](signal-score/step6-risk-penalty.md) | 리스크 패널티 |
| [signal-score/step7-signal-score-integration.md](signal-score/step7-signal-score-integration.md) | 종합 Signal Score 통합 + A/B/C 등급 분기 |
| [signal-score/step8-slack-integration.md](signal-score/step8-slack-integration.md) | 주식 알림 Slack 연동 배선 |
| [signal-score/step9-history-page.md](signal-score/step9-history-page.md) | Signal Score 이력 조회 페이지 + 점수식 확장 |

---

## 3. 개별 기능 / 화면

| 문서 | 상태 | 요약 |
|---|---|---|
| [trading-journal-features.md](trading-journal-features.md) | 📝 제안서 | 매매일지 고도화 — 복기 경험 강화 + 뇌동매매 방지 4가지 기능 |
| [kis-mcp-and-ranking-preview.md](kis-mcp-and-ranking-preview.md) | ✅ 완료 (2026-07-10) | 한국투자 MCP 연결 + 순위분석 API 미리보기 페이지 |
| [sector-index-psychology-index-fix.md](sector-index-psychology-index-fix.md) | ✅ 완료 (2026-07-15) | 업종 일자별지수 `net_buy` 필드 오라벨링 발견·수정 |
| [slack-login.md](slack-login.md) | 📖 학습용 정리 | Slack 연동 로그인 (상시 비밀번호 방식) 동작 정리 |

---

## 4. 워크플로우 / 가이드

| 문서 | 요약 |
|---|---|
| [claude-usage-guide.md](claude-usage-guide.md) | 이 프로젝트에서 Claude로 작업·리뷰할 때의 원칙 (작업도 리뷰도 Claude인 워크플로우) |
| [../upbit.md](../upbit.md) | 운영 가이드 — 서버 설치, PM2 프로세스 구성, Nginx, 배포, DB 테이블 |
| [../code.md](../code.md) | 코드 구조 — 데이터 흐름, 핵심 모듈, 확장 가이드 |

---

### 상태 아이콘 범례

✅ 완료 · ⏸️ 중단 · 🧪 검증만 완료 · 🔨 구현 중/부분 완료 · 📝 설계·제안 단계 · 📖 학습/참고용
