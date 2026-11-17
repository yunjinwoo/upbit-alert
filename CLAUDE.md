# upbit-alert

업비트·국내주식 알림/수집 + 실거래 자동매매(업비트, 토스증권). Python(Flask + SQLite), 운영 서버에서 PM2 로 프로세스 여러 개가 돈다.
기능별 설명은 [docs/README.md](docs/README.md) 가 목차다.

## 절대 하지 않는 것

이 저장소는 **실제 돈이 오가는 서버에 자동 배포**된다. 아래는 사람이 그 자리에서 명시적으로 시킨 경우에만 한다.

- **main 에 직접 push 하지 않는다.** main push = `.github/workflows/deploy.yml` 이 서버 코드를 덮어쓰고 실거래 봇까지 재시작한다. 변경은 항상 브랜치 + PR, 병합은 사람이 한다.
- **매매·설정을 바꾸는 요청을 보내지 않는다.** `/api/auto-trade/*`, `/api/toss-trade/*` 의 POST(toggle, run-now, force-buy, force-sell, approve, settings, dca 등)와 `/api/job-log/run/*`, `/api/login/*` POST. 로컬 서버라도 `.env` 에 실계좌 키가 있으면 실주문이 나갈 수 있다.
- **실거래 프로세스를 띄우지 않는다.** `python main.py live_trade`, `toss_live_trade`, `trade`, `toss_trade`, `all`.
- **`.env`, `credentials.json` 을 열어 보거나 출력하지 않는다.** 값이 필요하면 `Config` 를 거쳐 쓰는 스크립트를 실행한다.
- **`alerts.db` 를 직접 고치지 않는다.** 조회는 읽기 전용으로. 정리·마이그레이션은 사람이 실행한다.
- 자동 점검(스케줄 실행)은 **읽기만** 한다. 고칠 것이 보이면 원인과 수정안을 보고하고, 코드 수정은 PR 로만 올린다.

## 실행

```bash
.venv/Scripts/python.exe main.py api        # 로컬 대시보드(포트 5000) — 매매 루프는 안 돈다
.venv/Scripts/python.exe tests/test_health.py
.venv/Scripts/python.exe check_health.py    # 운영 서버 상태 점검(읽기 전용)
```

- 테스트는 파일마다 `python tests/test_xxx.py` 로 실행한다(venv 에 pytest 없음). 일부 파일은 pytest 형식만 있다.
- 테스트는 `db_manager.DB_PATH` 를 임시 파일로 바꿔서 돌린다. 운영/로컬 `alerts.db` 를 쓰는 테스트를 만들지 않는다.
- 로컬 `alerts.db`·`app.log` 는 오래된 사본일 수 있다. 지금 상태는 운영 서버에서만 알 수 있다 → `check_health.py`.

## 구조

- `main.py` — 모드별 진입점. `app/api/server.py` — Flask 라우트 전부. `app/utils/db_manager.py` — SQLite 접근 전부.
- `app/core/` — 수집(`stock_monitor`, `upbit_monitor`, `*_market_analysis`), 매매(`auto_trader`, `toss_auto_trader`, `brokers/`), 요약·점검(`stock_digest`, `health`).
- 수집 스케줄은 `stock_monitor.run_stock_monitor` 안의 시각 조건에 있고, 실행 결과는 `job_run_log` 테이블에 쌓인다.

## 상태 점검

- `GET /api/health` (`app/core/health.py`) — 수집 테이블별 마지막 저장 시각, 최근 24시간 스케줄 실패, 로그 오류 요약. 로그인 세션 또는 `X-Health-Token`(`HEALTH_API_TOKEN`)으로 인증한다.
- `/monitor` (`templates/monitor.html`) — 같은 응답을 표로 보여주는 화면. 시장 판단·주요 지수 메시지도 여기에 있다.
- `check_health.py` 종료 코드: 0 정상, 1 확인 필요, 2 서버에 못 닿음/인증 실패.
- 판정 기준은 `health.FRESHNESS_RULES` 에 있다. 수집 시각을 바꾸면 여기도 같이 고친다.
- 공휴일은 모른다. 평일 휴장일엔 주식 수집이 `stale` 로 나오는 게 정상이다.
- `stale` 을 봤을 때 순서: `jobs.failing` 의 오류 문구 → `log.recent_errors` → 해당 `process`(PM2 이름)의 수집 코드.

## 코드 관례

- 주석·문서·커밋 메시지는 한국어. 함수에는 "왜 이렇게 했는지"를 적는 독스트링이 많다 — 고칠 때 같이 고친다.
- 새 기능은 `docs/` 에 문서 한 장을 두고 `docs/README.md` 목차에 한 줄 추가한다.
