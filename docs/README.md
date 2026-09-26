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
| [auto-trade-tight-stop.md](auto-trade-tight-stop.md) | ✅ 구현 완료 · 기본 꺼짐 | 짧은 손절 · 긴 수익 — 손실을 끊는 폭과 수익을 지키는 폭을 분리(전환 전 평단 대비 짧은 손절 → 전환 후 고점 대비 긴 트레일링, 물타기 없음) |
| [auto-trade-accumulate.md](auto-trade-accumulate.md) | ✅ 구현 완료 · 기본 꺼짐 | 모아가기 — 등록 코인(BTC/ETH 등)은 정밀 매수조건을 통과하면 정해둔 금액만큼 매수(코인별 간격에 1번), 자동 매도 없음 |
| [auto-trade-convergence.md](auto-trade-convergence.md) | ✅ 구현 완료 · **기본 켜짐**(하루 1종목) | 수렴 자동 매수 — 승인 없이 봇이 고름: 24h 거래대금 400억↑ 새 코인 중 5분봉 가격·구름·80선·120선이 1.5% 안에 모이고 일봉 구름·기준선 위면 매수, 이후 일반 청산 규칙 |
| [auto-trade-downside-watch.md](auto-trade-downside-watch.md) | ✅ Phase 1 머지됨 (PR #62, 2026-09-03) · Phase 2 미정 | 하락위험 코인 관심목록 — 매수 파이프라인의 거울상, 표시 전용 |
| [auto-trade-performance.md](auto-trade-performance.md) | ✅ 머지됨 (PR #71, 2026-09-18) | 매매 성과 화면 — 매수→매도를 한 사이클로 묶어 승률·누적 손익·진입 신호별 기여도 조회 (읽기 전용) |
| [auto-trade-market-indicators.md](auto-trade-market-indicators.md) | ✅ 구현 완료 (2026-09-23) · 주봉 RSI 조건 기본 꺼짐 | 정밀 매수조건 "주봉 RSI ≥ 65" + 자동매매 화면 시장 지표 카드(BTC RSI 4시간봉/일봉/주봉 · BTC 도미넌스, 표시 전용) |
| [market-regime.md](market-regime.md) | ✅ 구현 완료 (2026-09-24) · 표시·알림 전용 | 시장 판단(좋음/애매/나쁨) — BTC 20일선·RSI·상승 종목 비율 점수제, 시장 지표 카드 표시 + 판단이 바뀌면 슬랙 알림 + 전략 묶음(좋음/애매/나쁨) 버튼으로 청산 값 한 번에 적용(사람이 직접 고름) |

| [coin-ranking-history.md](coin-ranking-history.md) | ✅ 구현 완료 (2026-09-24) · 조회 전용 | 코인 당일 순위 이력 — 상승률/거래대금 상위를 매시 저장해 15일 보관, 종목 × 날짜 표로 마감 순위·장중 등장 표시 |
| [trade-journal-api.md](trade-journal-api.md) | ✅ 구현 완료 (2026-09-24) · 조회 전용 | 매매일지(stock-history) 연동 API — 코인 체결마다 매수/매도 사유·시장 판단·보유 중 최고/최저 수익률을 돌려줌(같은 서버에서만 호출) |
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
| [code-study-guide.md](code-study-guide.md) | 📖 **코드 공부 가이드** — 처음 읽는 사람용. 폴더 지도, 매매 사이클 한 바퀴, 최근 기능(재매수 대기·모아가기·강제매도 승인 해제·수렴 자동 매수)의 코드 위치, 추천 읽기 순서 |
| [claude-usage-guide.md](claude-usage-guide.md) | 이 프로젝트에서 Claude로 작업·리뷰할 때의 원칙 (작업도 리뷰도 Claude인 워크플로우) |
| [../upbit.md](../upbit.md) | 운영 가이드 — 서버 설치, PM2 프로세스 구성, Nginx, 배포, DB 테이블 |
| [../code.md](../code.md) | 코드 구조 — 데이터 흐름, 핵심 모듈, 확장 가이드 |

---

### 상태 아이콘 범례

✅ 완료 · ⏸️ 중단 · 🧪 검증만 완료 · 🔨 구현 중/부분 완료 · 📝 설계·제안 단계 · 📖 학습/참고용
