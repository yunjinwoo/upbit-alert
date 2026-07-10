# Signal Score 기획 진행 상황

> 급등 탐지기 → 신호 등급화 시스템으로 확장하는 작업 추적용 문서.
> 난이도가 낮은 순서대로 진행. 각 단계 완료 시 상태와 날짜, 관련 파일/커밋을 기록.

## 배경
- 기존 데이터 구조 검토 결과, 수급(외국인/기관)·시가총액·업종지수 데이터는 이미 DB에 존재.
- 부족한 부분: 20일 평균 거래량 비율 계산, Signal Score 산출 로직, A/B/C 등급 분기, 주식 알림 Slack 배선, 투자자 매매동향 전종목 수집.
- 관련 기존 코드: `app/utils/db_manager.py`(`stock_investor_daily`, `stock_market_cap_daily`, `sector_index_daily`, `get_investor_ranking`, `get_investor_cross_distribution`), `app/core/stock_monitor.py`(KIS API 수집), `app/core/upbit_monitor.py`(`send_slack_msg`).

## 진행 순서 (간단 → 복잡)

| 순서 | 작업 | 상태 | 비고 |
|---|---|---|---|
| 1 | 종목별 20일 평균 거래량 대비 당일 거래량 배수 계산 함수 | ✅ 완료 (2026-07-09) | `volume` 컬럼 신규 추가 필요했음. 상세: [step1-volume-ratio.md](step1-volume-ratio.md) |
| 2 | 모멘텀 점수(거래량+등락률) 산출 함수 | ✅ 완료 (2026-07-10) | 상세: [step2-momentum-score.md](step2-momentum-score.md) |
| 3 | 수급 점수(외국인/기관 3일 누적) 산출 함수 | 대기 | 기존 `get_investor_ranking` 등 재사용, 30점 만점 |
| 4 | 시총/랭킹 안정성 점수 산출 함수 | 대기 | `stock_market_cap_daily` 랭킹 변화 diff 계산 추가, 15점 만점 |
| 5 | 시장/업종 환경 점수 산출 함수 | 대기 | `sector_index_daily` 활용, 15점 만점 |
| 6 | 리스크 패널티 함수 | 대기 | 최근 5일 급등/윗꼬리/동반매도 감점, -20점 |
| 7 | 종합 Signal Score 통합 + A/B/C 등급 분기 | 대기 | 1~6 결과 합산, 등급 테이블 저장 |
| 8 | 주식 알림 Slack 연동 배선 | 대기 | 코인용 `send_slack_msg` 재사용, 등급별 알림 조건 연결 |
| 9 | 투자자 매매동향 수집 대상 전종목 확대 (선택) | 대기 | API 호출량/레이트리밋 고려 필요 |

## 상태 값 정의
- 대기: 아직 착수 안 함
- 진행중: 작업 중
- 완료: 구현 및 확인 완료 (커밋 해시 기록)
- 보류: 우선순위 밀림 또는 재검토 필요

## 로그
- 2026-07-09: 문서 생성, 작업 순서 정리.
- 2026-07-09: 1번 작업(거래량 배수 계산 함수) 완료. 상세 내용은 [step1-volume-ratio.md](step1-volume-ratio.md) 참고.
- 2026-07-10: `feature/signal-score` 브랜치 생성, 1번 작업 커밋(`a2d1e98`).
- 2026-07-10: 2번 작업(모멘텀 점수 산출 함수) 완료. 상세 내용은 [step2-momentum-score.md](step2-momentum-score.md) 참고.
