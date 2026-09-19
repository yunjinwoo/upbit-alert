# 🚀 통합 금융 알림·자동매매 시스템 운영 가이드 (Upbit & KIS & 토스증권)

본 문서는 서버 운영(설치·프로세스·배포·접속) 관점의 가이드다. 알림에서 시작했지만 지금은
업비트·토스증권 **실거래 자동매매**까지 같은 저장소에서 돌아간다 — 매매 엔진의 설계/의사결정
기록은 [docs/README.md](docs/README.md) 색인을 보고, 코드 구조 설명은
[code.md](code.md)를 본다.

## 1. 프로젝트 구조 및 파일 역할

2026-04-09 리팩터링으로 루트에 흩어져 있던 파일들이 `app/` 패키지로 들어갔다.
실행 진입점은 `main.py` 하나이며 인자로 모드를 고른다(`python main.py <mode>`).

| 경로 | 역할 |
| :--- | :--- |
| `main.py` | 통합 진입점 — `all`/`api`/`upbit`/`stock`/`coin_analysis`/`trade`/`live_trade`/`toss_*` 등 모드 분기 |
| `app/config.py` | 환경변수·임계값 등 설정 중앙 집중 |
| `app/api/server.py` | Flask 대시보드 서버 + JSON API 전체 |
| `app/core/upbit_monitor.py` | 업비트 거래량 급증 감시(알림) |
| `app/core/stock_monitor.py` | 한국투자증권(KIS) 국내주식 감시 + Signal Score 수집 |
| `app/core/upbit_market_analysis.py` / `toss_market_analysis.py` | 매매 후보 스크리닝(지표 계산 → `*_screening_daily`) |
| `app/core/auto_trader.py` / `toss_auto_trader.py` | 매매 사이클 오케스트레이션(청산 판단 → 진입 판단 → 실행/기록) |
| `app/core/trade_strategy.py` / `entry_conditions.py` / `exit_conditions.py` | 진입·청산 판단 **순수 함수**(DB·네트워크 접근 없음) |
| `app/core/brokers/` | 거래소별 주문 구현 — `paper_broker`(모의), `upbit_live_broker`/`toss_live_broker`(실거래) |
| `app/utils/db_manager.py` | SQLite(`alerts.db`) 스키마·마이그레이션·조회 전부 |
| `app/utils/logger.py` / `slack.py` / `google_sheets.py` / `network.py` | 로그·슬랙·구글시트·네트워크 유틸 |
| `templates/` | 대시보드 화면(부트스트랩/DataTables). `_navbar.html`이 공통 상단 메뉴 |
| `docs/` | 기능별 설계·구현 기록 ([색인](docs/README.md)) |
| `.env` | API Key 및 토큰 등 민감 정보 (Git 제외) |
| `requirements.txt` | 설치된 파이썬 패키지 목록 |

> 루트의 `main_stocks.py`·`api_server.py`·`db_manager.py`·`save_alert.py`는 리팩터링 때
> 없어졌다. 오래된 문서나 메모에서 이 이름이 보이면 위 표의 `app/...` 경로로 읽으면 된다.

---

## 2. 서버 환경 설정 (iwinv / Ubuntu)

### 필수 패키지 설치
```bash
sudo apt update
sudo apt install python3-pip python3-venv nodejs npm nginx -y
sudo npm install -g pm2
```

### 파이썬 가상환경 (venv)
```bash
cd ~/upbit-alert
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## 3. 데이터베이스 구조 (SQLite)

`alerts.db` 하나에 전부 들어간다. 테이블 생성과 컬럼 추가(마이그레이션)는 `app/utils/db_manager.py`가
시작 시 자동으로 처리하므로 수동 DDL은 필요 없다. 주요 테이블만 추리면:

| 테이블 | 용도 |
| :--- | :--- |
| `alerts` / `stock_alerts` | 코인·주식 거래량 급증 알림 이력 |
| `coin_screening_daily` / `stock_screening_daily` | 매매 후보 스크리닝 결과(신호 플래그 포함) |
| `trade_candidate_approval` | 종목별 관심등록(watchlist)·실거래 승인 체크 상태 |
| `trade_engine_settings` | (broker, mode)별 실행 on/off 스위치와 하트비트 |
| `trade_strategy_settings` | 매수금액·손절·익절·루프주기 등 매매 기준 |
| `paper_positions` / `paper_account` | 포지션 추적 — 모의는 가상 원장, 실거래는 트레일링/물타기 추적용 |
| `trade_order_log` | 매매 판단·체결 감사로그(BUY/SELL/HOLD/SKIP/DCA_BUY) |
| `stock_investor_daily` / `stock_market_cap_daily` / `sector_index_daily` | Signal Score 산출용 수집 데이터 |

> 실거래와 모의매매는 같은 테이블을 쓰되 `(broker, mode)` 키로 완전히 분리 저장된다 —
> 실거래 이력은 `mode='live'`.

---

## 4. 프로세스 관리 (PM2)
터미널을 꺼도 프로그램이 계속 돌아가도록 관리합니다.

배포(`.github/workflows/deploy.yml`)가 아래 7개를 매번 지웠다가 다시 띄운다.

| 프로세스 명 | 실행 모드 | 설명 |
| :--- | :--- | :--- |
| `upbit-api` | `main.py api` | 대시보드 서버 (Port: 5000) |
| `upbit-bot` | `main.py upbit` | 코인 거래량 감시 봇 |
| `stock-bot` | `main.py stock` | 국내주식 감시 봇 (장 운영 시간) |
| `coin-analysis-bot` | `main.py coin_analysis` | 코인 매매 후보 스크리닝 (30분 주기) |
| `upbit-live-trade-bot` | `main.py live_trade` | 🔴 업비트 실거래 자동매매 |
| `toss-analysis-bot` | `main.py toss_analysis` | 국내주식 매매 후보 스크리닝 |
| `toss-live-trade-bot` | `main.py toss_live_trade` | 🔴 토스증권 실거래 자동매매 |

> 모의매매 프로세스(`trade-bot`, `condition-check-bot`, `toss-trade-bot`,
> `toss-condition-check-bot`)는 실거래로 넘어가면서 자동 시작 목록에서 빠졌다. 코드와 DB는 남아
> 있고 루프에 idle 가드가 걸려 있어, 수동으로 띄워도 매매나 이력 기록을 하지 않는다.
>
> 실거래 봇이 떠 있다고 바로 주문이 나가는 게 아니다 — 대시보드의 "실거래 실행" 스위치(기본 꺼짐)와
> 종목별 "실거래 승인" 체크가 둘 다 켜져 있어야 한다
> ([업비트](docs/auto-trade-upbit-live.md) · [토스증권](docs/auto-trade-toss-live.md)).

**주요 명령어:**
```bash
pm2 list                      # 전체 상태 확인
pm2 restart all               # 모든 프로세스 재시작
pm2 logs upbit-bot            # 특정 프로세스 로그 실시간 확인
pm2 save                      # 현재 설정을 부팅 시 자동 실행으로 저장
```

---

## 5. 웹 서버 및 도메인 설정 (Nginx)
80번 포트(HTTP)로 들어오는 요청을 5000번(Flask)으로 전달합니다.

- **설정 파일 위치**: `/etc/nginx/sites-available/upbit-alert`
- **심볼릭 링크**: `/etc/nginx/sites-enabled/upbit-alert`

**Nginx 명령어:**
```bash
sudo nginx -t                 # 설정 파일 문법 검사
sudo systemctl reload nginx   # 설정 반영 (서비스 중단 없음)
sudo tail -f /var/log/nginx/access.log  # 접속 기록 실시간 확인
```

---

## 6. CI/CD 자동 배포 (GitHub Actions)
내 PC에서 코드를 수정하고 GitHub에 `push` 하면 자동으로 서버에 반영됩니다.

- **설정 파일**: `.github/workflows/deploy.yml`
- **필요한 GitHub Secrets**:
  - `SERVER_IP`: 서버 공인 IP
  - `SERVER_USER`: 접속 계정 (`deploy-user`)
  - `SSH_PRIVATE_KEY`: `id_ed25519` 비밀키 내용

---

## 7. 보안 및 접속 정보
- **대시보드 접속**: `http://서버IP/` 또는 연결된 도메인
- **보안 설정 (Nginx Basic Auth)**:
  - ID/PW 설정: `sudo htpasswd -c /etc/nginx/.htpasswd admin`
  - 접속 시 브라우저 팝업창을 통해 인증 후 사용 가능

---

## 8. 기타 팁
- **주식 봇**: 평일 09:00 ~ 15:30 외에는 대기 모드로 작동합니다.
- **데이터 삭제**: 대시보드 우측의 🗑️ 버튼을 누르면 DB에서 즉시 삭제됩니다.
- **실시간 검색**: 대시보드 상단 TOP 5를 클릭하면 해당 종목만 필터링됩니다.
