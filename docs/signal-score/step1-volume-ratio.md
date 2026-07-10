# 1단계: 종목별 20일 평균 거래량 대비 당일 거래량 배수 계산

- 상태: 완료
- 날짜: 2026-07-09
- 관련 파일: `app/utils/db_manager.py`

## 작업 배경
`stock_market_cap_daily` 테이블에 저장되는 시가총액 순위 데이터에 KIS API가 주는 누적거래량(`acml_vol`)이 있었지만, 저장 함수(`save_daily_market_cap`)가 이 값을 버리고 저장하지 않고 있었음. 그래서 거래량 배수를 계산하려 해도 원본 데이터 자체가 DB에 없는 상태였음.

## 변경 내용

1. **`stock_market_cap_daily` 테이블에 `volume` 컬럼 추가**
   - `CREATE TABLE` 정의에 `volume TEXT` 추가
   - 기존 DB에는 `ALTER TABLE ... ADD COLUMN volume TEXT DEFAULT "0"` 마이그레이션으로 반영 (기존 데이터는 전부 `0`으로 채워짐 — 과거분은 원본 거래량 값이 없으므로 소급 복구 불가, 앞으로 수집되는 날짜부터 정상 값 저장됨)

2. **`save_daily_market_cap()` 수정**
   - `item.acml_vol`을 `volume` 컬럼에 저장하도록 INSERT 문 변경

3. **`sync_upsert_market_cap()` 수정**
   - 서버 동기화 시에도 `volume` 필드를 함께 upsert 하도록 변경 (로컬 ↔ 서버 데이터 동기화 기능과의 정합성 유지)

4. **신규 함수 2개 추가**
   - `get_volume_ratio(code, avg_days=20)`
     - 특정 종목의 최근 `avg_days`일 평균 거래량 대비 당일 거래량 배수 계산
     - 반환: `{code, date, today_volume, avg_volume, ratio, days_used}`
     - `days_used`가 `avg_days`보다 작으면 아직 데이터가 충분히 쌓이지 않았다는 뜻(참고용)
   - `get_volume_ratio_batch(date=None, avg_days=20, fid_input_iscd="combined")`
     - 특정 날짜(기본: 최신일) 기준 전 종목의 거래량 배수를 일괄 계산해 배수 내림차순으로 반환
     - 2단계(모멘텀 점수 산출)에서 바로 재사용할 목적으로 함께 추가

## 검증
- 실제 DB(`alerts.db`)에 테스트 종목코드로 가짜 거래량 데이터(과거 5일 평균 100,000 + 당일 123,456)를 넣고 `get_volume_ratio()` 호출 → `ratio: 1.235` 정확히 계산됨 확인 후 테스트 데이터 삭제.
- `save_daily_market_cap()`에 모의 `MarketCapRankingItem`(`acml_vol='123456'`)을 넣어 실제로 `volume` 컬럼에 저장되는지 확인.
- 기존 DB 스키마 마이그레이션이 에러 없이 적용되고 `volume` 컬럼이 정상 추가됨을 `PRAGMA table_info`로 확인.

## 주의할 점 (다음 단계 참고용)
- 과거 데이터(2026-07-09 이전 수집분)는 `volume`이 전부 `0`이라 배수 계산에서 제외됨. 그래서 서비스 운영 중 매일 자동 수집되는 시점(오후 3시 40분, `stock_monitor.py`의 `run_stock_monitor`)부터 데이터가 쌓여야 배수가 정상적으로 나옴 — 최소 1~2일치는 있어야 의미 있는 비교가 되고, 20일치가 쌓이기 전까지는 `days_used`가 20보다 작은 상태로 표시됨.
- `get_volume_ratio_batch()`는 2단계(모멘텀 점수 30점 산출) 작업에서 그대로 재사용할 예정.

## 화면 추가 (사용자 요청으로 후속 작업)
계산 결과를 확인할 방법이 없어서 조회용 페이지를 추가함.

- 신규 페이지: `/volume-ratio` (`templates/volume_ratio.html`)
- 신규 API: `GET /api/volume-ratio` (`app/api/server.py`) — `get_volume_ratio_batch()` 호출, 쿼리파라미터 `date`, `avg_days`, `market`(코스피/코스닥/합산), `top_n`
- 기존 9개 페이지 네비게이션 바에 "거래량 배수" 링크 추가 (다른 페이지들과 이동 일관성 유지)
- 검증: 운영 중이던 `main.py all` 프로세스(업비트+주식 모니터링+API, 아침부터 실행 중)를 재시작해야 새 라우트가 반영되어, 사용자 승인 받고 재시작 진행. 재시작 후 `/volume-ratio` 200 응답, API 정상 동작 확인. 로그에 에러 없음.
- 현재는 오늘 수집된 거래량이 아직 없어(코드 수정 전 15:40 수집분이라 volume=0) 화면에 데이터가 비어 보임 — 내일 자동 수집부터 정상적으로 채워짐.

## 누적 현황 확인 기능 추가 (사용자 요청)
"20일치가 쌓여야 한다는데 지금 며칠 쌓였는지 확인할 수 없냐"는 질문에 답하기 위해, 매번 물어보지 않아도 화면에서 바로 확인할 수 있도록 추가.

- 신규 함수: `get_volume_collection_status()` (`app/utils/db_manager.py`) — `volume`이 실제 값(0이 아님)으로 채워진 날짜 수와 최신 날짜 반환
- 신규 API: `GET /api/volume-ratio/status`
- `volume_ratio.html`에 상단 배너 추가 — "N/20일 누적됨" 형태로 표시, 0일이면 "오늘 장마감 후 첫 수집부터 쌓이기 시작" 안내

### 확인 결과 (2026-07-10 12:40 기준)
- 실거래량 누적: **0일** — 어제(7/9) 코드 수정이 그날 수집(오후 3:40) 이후에 반영되어, 아직 실제 값이 찍힌 날짜가 없음
- 오늘 오후 3시 40분 수집부터 1일치 시작 예정

## 다음 단계
2번: 모멘텀 점수(거래량 배수 + 등락률 조합, 30점 만점) 산출 함수 — `get_volume_ratio_batch()`와 `stock_market_cap_daily.change_rate`를 조합해서 계산.
