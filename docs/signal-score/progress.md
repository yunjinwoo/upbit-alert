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
| 3 | 수급 점수(외국인/기관 3일 누적) 산출 함수 | ✅ 완료 (2026-07-10) | 상세: [step3-supply-demand-score.md](step3-supply-demand-score.md) |
| 4 | 시총/랭킹 안정성 점수 산출 함수 | ✅ 완료 (2026-07-10, 순서 착오로 3번보다 먼저 진행함) | 상세: [step4-rank-stability-score.md](step4-rank-stability-score.md) |
| 5 | 시장/업종 환경 점수 산출 함수 | ✅ 완료 (2026-07-10) | 상세: [step5-market-environment-score.md](step5-market-environment-score.md) |
| 6 | 리스크 패널티 함수 | ✅ 완료 (2026-07-10) | 상세: [step6-risk-penalty.md](step6-risk-penalty.md) |
| 7 | 종합 Signal Score 통합 + A/B/C 등급 분기 | ✅ 완료 (2026-07-10) | 상세: [step7-signal-score-integration.md](step7-signal-score-integration.md) |
| 8 | 주식 알림 Slack 연동 배선 | ✅ 완료 (2026-07-10) | 상세: [step8-slack-integration.md](step8-slack-integration.md) |
| 9 | Signal Score 이력 조회 페이지 + 점수식 확장(HTS조회상위/관심종목등록 보너스, 리스크패널티 부호 전환) | ✅ 완료 (2026-07-21) | 상세: [step9-history-page.md](step9-history-page.md) |
| 10 | 투자자 매매동향 수집 대상 전종목 확대 (선택) | 대기 | API 호출량/레이트리밋 고려 필요 |

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
- 2026-07-10: 순서 착오로 3번(수급 점수) 대신 4번(시총/랭킹 안정성 점수 산출 함수)을 먼저 완료함. 상세 내용은 [step4-rank-stability-score.md](step4-rank-stability-score.md) 참고. 실제 데이터(코스닥 30종목, 07-09 기준)로 검증 완료 — 거래량 데이터와 달리 랭킹 이력은 이미 1개월치 쌓여있어 바로 확인 가능했음.
- 2026-07-10: 3번 작업(수급 점수 산출 함수) 완료. 상세 내용은 [step3-supply-demand-score.md](step3-supply-demand-score.md) 참고. 투자자매매동향 데이터도 이미 쌓여있어 실제 68개 종목으로 검증함.
- 2026-07-10: 07-09 코스피(0001) 시총 데이터 누락 원인을 코드 레벨에서 규명(`stock_monitor.py`의 자동 수집 루프에 401/오류 시 재시도 로직 없음, [step4 문서](step4-rank-stability-score.md) 참고). 사용자 요청으로 수정은 보류하고 별도 브랜치에서 나중에 진행하기로 함.
- 2026-07-10: 5번 작업(시장/업종 환경 점수 산출 함수) 완료. 상세 내용은 [step5-market-environment-score.md](step5-market-environment-score.md) 참고. `sector_index_daily`(코스피/코스닥 지수)는 07-09 누락 없이 정상 수집되어 있어 실제 데이터로 검증함.
- 2026-07-10: 2·3·4·5번 작업을 단계별로 분리해서 커밋함(`a907755`, `60ce93b`, `12b5606`, `5b12041`).
- 2026-07-10: 6번 작업(리스크 패널티 함수) 완료. 상세 내용은 [step6-risk-penalty.md](step6-risk-penalty.md) 참고. 커밋(`2386b08`).
- 2026-07-10: 7번 작업(종합 Signal Score 통합 + 등급 분기) 완료. 상세 내용은 [step7-signal-score-integration.md](step7-signal-score-integration.md) 참고. 코스닥 30종목 실제 데이터로 검증 — 거래량 데이터 미누적으로 현재는 최대 C등급까지만 나옴(예상된 결과). 커밋(`d4d171c`).
- 2026-07-10: 8번 작업(주식 알림 Slack 연동 배선) 완료. 상세 내용은 [step8-slack-integration.md](step8-slack-integration.md) 참고. 실제 Slack 웹훅이 설정되어 있어 A등급 없는 상태로 안전하게 실행 검증만 하고, 실제 발송은 트리거하지 않음.
- 2026-07-14~07-21: 별도 세션들에서 점수식이 여러 차례 조정됨 — 리스크패널티 "5일 과열 -15점"→"7일 강한 상승 추세 +15점" 전환, HTS조회상위가점(0~10→0~20점) 및 관심종목등록가점(신규 0~10점) 도입, 두 가점 합산 "관심도 보너스" 최대 30점 상한 적용. 등급 기준(80/65/50)은 변경 없음. PR #6~#9로 반영.
- 2026-07-21: 9번 작업(Signal Score 이력 조회 페이지) 완료. 상세 내용은 [step9-history-page.md](step9-history-page.md) 참고. 일별 스냅샷 60건 + 날짜별 추이 64개 종목 실제 데이터로 검증.
