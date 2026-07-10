# 한국투자 MCP 연결 + 순위분석 API 미리보기 페이지

- 상태: 완료 (페이지 확인까지), Signal Score 편입은 다음 단계로 보류
- 날짜: 2026-07-10
- 관련 파일: `.mcp.json`, `app/core/stock_monitor.py`, `app/api/server.py`, `templates/ranking_preview.html`, 기존 템플릿 10개(네비게이션)

## 1. 한국투자(KIS) MCP 서버 연결

### 문제
`.mcp.json`에 `kis-code-assistant-mcp`(한국투자증권 공식 Open API 검색용 MCP)가 등록되어 있었지만 계속 "connecting" 상태에서 멈춰 툴이 하나도 로드되지 않음.

### 원인 (2가지, 순차적으로 발견)
1. 이 MCP 서버가 내부적으로 Python 패키지 매니저 `uv`를 요구하는데 시스템에 `uv`가 아예 설치돼 있지 않았음.
2. `uv` 설치 후에도 Python 3.12 런타임 설치가 `Missing expected target directory for Python minor version link` 에러로 계속 실패. 원인은 `uv`의 기본 설치 경로(`%APPDATA%\uv\python`)가 Claude 앱의 샌드박스(App Container) 폴더 리다이렉션 때문에 프로세스마다 실제 경로가 다르게 보이는 충돌이었음(`AppData\Roaming\uv\...` ↔ `AppData\Local\Packages\Claude_...\LocalCache\Roaming\uv\...`).

### 조치
- `uv` 설치: `irm https://astral.sh/uv/install.ps1 | iex` → `C:\Users\BIZWIZSYSTEM\.local\bin`
- Python 설치 경로를 리다이렉션 영향을 받지 않는 고정 경로로 변경: `UV_PYTHON_INSTALL_DIR=C:\Users\BIZWIZSYSTEM\.local\share\uv\python`
- `.mcp.json`의 `kis-code-assistant-mcp` 서버 설정에 `env.UV_PYTHON_INSTALL_DIR`를 명시해서 어떤 프로세스가 띄우든 항상 이 경로를 쓰도록 고정

### 결과
Claude Code 재시작 후 `kis-code-assistant-mcp` 툴(`search_domestic_stock_api`, `search_domestic_bond_api` 등 8개 API 카테고리 검색 + `read_source_code`)이 정상 로드됨. 국내주식 API 검색/코드 조회가 즉시 가능해짐.

## 2. 순위분석 API 후보 조사

기존 Signal Score 시스템(`app/core/stock_monitor.py`)은 순위분석 API 중 시가총액순위(`/ranking/market-cap`)와 등락률순위(`/ranking/fluctuation`) 2개만 사용 중이었음. KIS 순위분석 카테고리 전체(22개)를 MCP로 검색해서 Signal Score 5개 요소(모멘텀/수급/랭킹안정성/시장환경/리스크)에 추가로 꽂아 넣을 만한 후보를 추림.

| 후보 | 엔드포인트 | tr_id | 활용 방향 |
|---|---|---|---|
| 거래량순위 | `/uapi/domestic-stock/v1/quotations/volume-rank` | `FHPST01710000` | 기존 `volume_ratio.html`이 수작업 계산 중인 거래량비를 공식 API로 대체 |
| 체결강도 상위 | `/uapi/domestic-stock/v1/ranking/volume-power` | `FHPST01680000` | 모멘텀 점수 — 실제 매수세 강도 반영 |
| 이격도 순위 | `/uapi/domestic-stock/v1/ranking/disparity` | `FHPST01780000` | 모멘텀/리스크 — 과열·과매도 필터 |
| 공매도 상위종목 | `/uapi/domestic-stock/v1/ranking/short-sale` | `FHPST04820000` | 리스크 패널티 보강 |
| 대량체결건수 상위 | `/uapi/domestic-stock/v1/ranking/bulk-trans-num` | `FHKST190900C0` | 세력/기관 매매 포착 |
| 예상체결 상승/하락상위 | `/uapi/domestic-stock/v1/ranking/exp-trans-updown` | `FHPST01820000` | 장전 프리마켓 시그널 |

(신용잔고 상위/재무비율/시장가치 순위 등도 조사했으나 우선순위 낮음으로 보류)

## 3. `/ranking-preview` 미리보기 페이지 추가

정식으로 Signal Score에 편입하기 전에, 사용자가 실데이터를 먼저 눈으로 확인할 수 있도록 **DB 저장 없이 KIS API를 즉시 호출**해서 보여주는 페이지만 우선 추가함.

- 신규 함수: `fetch_ranking_preview(api_path, tr_id, params)` (`app/core/stock_monitor.py`) — 기존 `fetch_market_cap_ranking()`과 동일한 401 재시도 패턴을 재사용하는 범용 함수. 위 6개 후보 API를 이 함수 하나로 다 처리.
- 신규 라우트: `GET /ranking-preview`(페이지), `GET /api/ranking-preview?type=...`(`app/api/server.py`) — `RANKING_PREVIEW_TYPES` 딕셔너리에 6개 후보의 path/tr_id/기본 파라미터 정의.
- 신규 템플릿: `templates/ranking_preview.html` — 드롭다운으로 API 선택 → 조회. 응답 필드가 API마다 달라서 테이블 컬럼을 응답 JSON 키 기준으로 동적 생성.
- 기존 10개 페이지 네비게이션 바에 "순위분석 미리보기" 링크 추가.

## 검증
- 6개 타입 전부 `/api/ranking-preview?type=...` 실제 호출로 확인 — 전부 `status: success`, 정상 건수(대부분 30건, 대량체결건수는 15건) 반환.
- 페이지 HTML에 드롭다운 6개 옵션과 네비게이션 활성 링크가 정상 렌더링되는 것 확인.

### 운영 중 이슈 (해결됨)
PyCharm에서 띄워둔 라이브 프로세스(`main.py`, 업비트+주식 실시간 모니터링 + API 대시보드 동시 실행)가 리로더 없이(`use_reloader=False`) 돌고 있어서, 템플릿에 `url_for('ranking_preview_view')`를 추가하자마자 그 엔드포인트를 모르는 구버전 프로세스가 **대시보드 전체 페이지에서 500 에러**를 냄. 사용자 승인 받고 프로세스 재시작(`.venv/Scripts/python.exe main.py`)해서 정상화 확인.

## 다음 단계
- 6개 후보 중 실제로 쓸만한 것을 사용자가 선정하면, 해당 API만 `stock_monitor.py`의 정식 수집 함수로 승격(DB 저장 + Signal Score 요소에 반영).
- `fetch_ranking_preview()`는 미리보기 전용이므로, 정식 편입 시에는 `fetch_market_cap_ranking()`처럼 DB 저장 로직이 포함된 별도 함수로 새로 작성 예정.
