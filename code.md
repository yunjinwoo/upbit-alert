# 💻 시스템 코드 설계 및 작동 원리 (Code Architecture)

이 문서는 코인/주식 알림 시스템의 데이터 흐름과 핵심 로직을 코드 관점에서 설명합니다.

---

## 1. 데이터 흐름도 (Data Flow)
1.  **감시 봇 (`main.py`, `main_stocks.py`)**: API를 통해 실시간 데이터를 수집하고 조건 검사.
2.  **데이터 저장 (`db_manager.py`)**: 조건 만족 시 SQLite DB에 구조화된 데이터 저장.
3.  **외부 알림 (`save_alert.py`)**: 동시에 슬랙 전송 및 구글 시트에 기록.
4.  **웹 대시보드 (`api_server.py`)**: Flask 서버가 DB 데이터를 JSON으로 제공.
5.  **프론트엔드 (`index.html`)**: 브라우저에서 JSON 데이터를 받아 탭별로 렌더링.

---

## 2. 핵심 모듈 상세 분석

### ① `db_manager.py` (데이터의 심장)
- **`init_db()`**: `CREATE TABLE IF NOT EXISTS`를 사용하여 테이블 자동 생성 보장.
- **`save_alert_to_db()` & `save_stock_alert_to_db()`**: 실시간 알림을 DB로 영구 저장.
- **`get_latest_alerts()`**: 대시보드 성능을 위해 최근 50개만 잘라서 가져오는 효율적인 쿼리 수행.
- **`delete_alert()`**: 행(Row)별 ID를 기준으로 개별 삭제 기능 구현.

### ② `api_server.py` (데이터 중계소)
- **`@app.route('/')`**: `render_template`을 통해 웹 대시보드 메인 화면 출력.
- **`@app.route('/alerts')`**: 코인 알림 데이터를 JSON 형식으로 변환하여 프론트엔드에 전달.
- **`@app.route('/stock-alerts', methods=['GET', 'DELETE'])`**: 주식 데이터 조회 및 삭제를 위한 RESTful API 구현.
- **`CORS(app)`**: 다른 도메인이나 IP에서의 접속 시 보안 차단을 방지하기 위해 필수 설정.

### ③ `main.py` (코인 감시 로직)
- **거래량 배수 체크**: `curr_vol / prev_vol`을 통해 갑작스러운 거래량 폭발 감지.
- **전 타임프레임 감시**: 4시간, 1시간, 30분, 15분 봉을 동시에 체크하여 신뢰도 상승.
- **중복 방지**: `last_notified_time` 딕셔너리를 사용하여 동일한 봉에서의 중복 알림 차단.
- **스킵 캐시**: `skip_cache`를 통해 일봉 거래량이 부족한 종목은 일정 시간 분석 제외하여 API 부하 방지.

### ④ `main_stocks.py` (주식 감시 로직)
- **OAuth2 토큰**: 한국투자증권 전용 토큰(`get_access_token`)을 자동으로 발급받아 세션 유지.
- **순위 API (`FHPST01710000`)**: 증권사 서버에서 계산된 거래량 순위 데이터를 단일 호출로 획득.
- **장 시간 제어**: `datetime.now()`를 체크하여 평일 장 운영 시간(09:00~15:30)에만 루프 가동.

---

## 3. 프론트엔드 핵심 로직 (`index.html`)

### 탭 전환 시스템 (`switchTab`)
- `currentTab` 변수 하나로 코인/주식 모드 전환.
- 탭 클릭 시 테이블의 헤더(`tableHead`)와 바디(`tableBody`) 구조를 동적으로 재구성.

### 실시간 데이터 렌더링 (`loadData`)
- `fetch()`를 사용하여 비동기로 JSON 데이터 수신.
- **DataTables 라이브러리**: 수신된 데이터를 기반으로 `dataTable.destroy()` 후 재초기화하여 정렬 및 검색 기능 활성화.
- **TOP 5 집계**: 자바스크립트의 `reduce` 또는 `forEach`를 이용해 화면에 표시된 데이터 중 가장 빈번한 티커/종목명을 실시간 추출.

---

## 4. 확장 가이드 (코드 수정 팁)
- **알림 조건 변경**: `main.py`의 `thresholds` 딕셔너리나 `main_stocks.py`의 `vol_rate > 300` 부분을 수정.
- **데이터 보관 개수**: `db_manager.py`의 `get_latest_alerts(limit=50)`의 숫자를 늘려 더 긴 히스토리 조회 가능.
- **새로운 알림 추가**: `db_manager.py`에 테이블 추가 -> `api_server.py`에 엔드포인트 추가 -> `index.html`에 탭 추가 순서로 진행.

===========

# 리펙토링 20260409 
전체적인 리펙토링과 테스트 파일 정리를 완료했습니다. 주요 변경 사항은 다음과 같습니다:

   1. 프로젝트 구조 모듈화: app 폴더를 생성하여 코드를 역할별로 분리했습니다.
       * app/api/: Flask 서버 관련 코드 (server.py)
       * app/core/: 업비트 및 주식 감시 핵심 로직 (upbit_monitor.py, stock_monitor.py, kis_models.py)
       * app/utils/: 데이터베이스, 로깅, 구글 시트 등 유틸리티 (db_manager.py, logger.py, google_sheets.py)
   2. 설정 중앙 집중화: app/config.py를 생성하여 환경 변수와 주요 설정값들을 한 곳에서 관리하도록 했습니다.
   3. 테스트 파일 분리: test_insert.py를 tests/ 폴더로 이동하고, 새로운 구조에서도 작동하도록 업데이트했습니다.
   4. 통합 엔트리 포인트: 루트의 main.py를 수정하여 인자값(api, upbit, stock, all)에 따라 원하는 서비스를 실행할 수 있도록 통합했습니다.
   5. 배포 워크플로우 업데이트: .github/workflows/deploy.yml을 새로운 구조와 실행 방식에 맞게 수정했습니다.
   6. 코드 정리: 루트 디렉토리에 흩어져 있던 기존 파일들을 정리하여 프로젝트가 훨씬 깔끔해졌습니다.
---
  이제 서비스를 실행하려면 다음과 같이 사용할 수 있습니다:
   * 전체 실행: .venv\Scripts\python.exe main.py
   * 특정 기능만 실행: .venv\Scripts\python.exe main.py api (또는 upbit, stock)
   * 테스트 데이터 입력: .venv\Scripts\python.exe tests\test_insert.py

 서버 부하를 줄이기 위해 감시 로직의 딜레이 시간을 기존보다 4배 늘렸습니다.

  변경 사항 요약

  1. 업비트 감시 (app/core/upbit_monitor.py)
   * 개별 코인 체크 사이: 0.5초 → 2.0초
   * 분봉 데이터 조회 사이: 0.1초 → 0.4초
   * 전체 코인 1회 순회 후 대기: 15분 → 1시간

  2. 주식 감시 (app/core/stock_monitor.py)
   * 실시간 순위 조회 간격: 30초 → 120초 (2분)
   * 장 종료 후 대기 간격: 1분 → 4분
   * 주말 대기 간격: 1시간 → 4시간
   * 에러 발생 시 재시도: 10초 → 40초

  이 설정을 통해 API 호출 횟수가 대폭 줄어들어 서버 리소스를 아끼고, API 제공처(업비트, 한국투자증권)의 호출 제한(Rate Limit)에 걸릴 위험도
  낮아졌습니다.

  만약 감시가 너무 느리다고 느껴지시면 다시 조정해 드릴 수 있습니다. 다른 필요한 사항이 있으시면 말씀해 주세요!