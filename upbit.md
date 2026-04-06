# 🚀 통합 금융 알림 시스템 운영 가이드 (Upbit & KIS)

본 문서는 Upbit 코인 알림과 한국투자증권(KIS) 주식 알림 시스템의 구조와 운영 방법을 정리한 문서입니다.

## 1. 프로젝트 구조 및 파일 역할
- `main.py`: Upbit 코인 거래량 감시 봇 (24시간 실행)
- `main_stocks.py`: 한국 주식 거래량 감시 봇 (장 운영 시간 실행)
- `api_server.py`: 대시보드 웹 서버 및 JSON API (Flask)
- `db_manager.py`: SQLite 데이터베이스(`alerts.db`) 관리 및 테이블 생성
- `save_alert.py`: 구글 시트 및 슬랙 전송 로직
- `logger.py`: 시스템 로그 관리 (`app.log`)
- `templates/index.html`: 웹 대시보드 UI (부트스트랩/DataTables 사용)
- `.env`: API Key 및 토큰 등 민감 정보 (보안상 Git 제외)
- `requirements.txt`: 설치된 파이썬 패키지 목록

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
`alerts.db` 파일 내에 두 개의 테이블이 자동 생성됩니다.
- `alerts`: 코인 알림 저장 (시간, 티커, 상태, 분봉 정보 등)
- `stock_alerts`: 주식 알림 저장 (시간, 종목명, 현재가, 등락률, 거래량 등)

---

## 4. 프로세스 관리 (PM2)
터미널을 꺼도 프로그램이 계속 돌아가도록 관리합니다.

| 프로세스 명 | 실행 파일 | 설명 |
| :--- | :--- | :--- |
| `upbit-bot` | `main.py` | 코인 감시 봇 |
| `stock-bot` | `main_stocks.py` | 주식 감시 봇 |
| `upbit-api` | `api_server.py` | 대시보드 서버 (Port: 5000) |

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
