# Slack 연동 로그인 기능 정리 (학습용)

앱 전체를 잠글지 말지를 토글하고, 잠그면 Slack으로 비밀번호를 보내주는 방식의 로그인 기능.
"OTP를 매번 새로 받는 방식"이 아니라 **"잠금을 켤 때 한 번 발급한 비밀번호를, 끄기 전까지 계속 재사용"**하는 상시 비밀번호 방식이다.

## 한눈에 보는 흐름

```
[/security] 잠금 토글 ON
        │
        ▼
새 비밀번호 생성 (숫자 7자리) → 해시로 DB 저장 → 평문을 Slack 웹훅으로 전송
        │
        ▼
사용자가 아무 페이지나 접속 → before_request가 세션 미로그인 감지 → /login 으로 리다이렉트
        │
        ▼
[/login] Slack으로 받은 비밀번호 입력 → /api/login/verify
        │
        ▼
해시 일치 확인 → 세션에 logged_in=True 기록 (30일 유지) → 이후 재입력 없이 이용
```

## 관련 파일 · 함수

### 1. `app/utils/db_manager.py` — 상태 저장 (DB)

| 대상 | 역할 |
|---|---|
| `login_settings` 테이블 (`init_db()` 안에서 생성) | 싱글톤 1행(`id=1`)에 `lock_enabled`(0/1), `password_hash`, `updated_at` 저장 |
| `get_login_settings()` | 현재 잠금 상태 + 비밀번호 해시 조회. 행이 없으면(최초 실행) `{'lock_enabled': True, 'password_hash': None}` 반환 — **기본값은 잠금 켜짐** |
| `save_login_settings(lock_enabled, password_hash=None)` | upsert. `password_hash=None`으로 호출하면 기존 해시를 그대로 유지(잠금만 끌 때 사용) |

서버가 재시작돼도 잠금 상태가 유지되는 이유가 여기 있다 — 예전 OTP 방식은 메모리에만 있어서 재시작하면 풀렸는데, 지금은 DB에 영구 저장.

### 2. `app/utils/slack.py` — Slack 전송

| 함수 | 역할 |
|---|---|
| `send_slack_msg(text)` | `Config.SLACK_WEBHOOK_URL`(`.env`의 `SLACK_TOKEN`)로 POST. 웹훅 미설정 시 경고 로그만 남기고 조용히 무시 |

API 서버 프로세스가 로그인 코드 전송 하나만을 위해 `gspread` 등 무거운 의존성(`app/core/upbit_monitor.py`)을 끌고 오지 않도록 별도 모듈로 분리돼 있다.

### 3. `app/config.py` — 설정값

| 값 | 의미 |
|---|---|
| `SLACK_WEBHOOK_URL` | `.env`의 `SLACK_TOKEN` 값을 웹훅 URL로 사용 |
| `SECRET_KEY` | Flask 세션 서명 키. **`.env`에 고정값으로 안 넣으면 서버 재시작마다 기존 세션이 다 풀림** |
| `SESSION_LIFETIME_DAYS = 30` | 로그인 세션 유지 기간 |

### 4. `app/api/server.py` — 인증 로직 본체 (제일 중요)

전역 상태(모듈 로드 시 1회):
- `_login_state = get_login_settings()` — DB에서 로드해 메모리에 캐시. 이후 로그인 API들은 이 캐시를 갱신하고 DB에도 같이 반영
- `_login_fail_count`, `_login_locked_until` — 무차별 대입 방지용 실패 카운터
- `_login_request_last_ts` — "비밀번호 받기" 버튼 남용 방지 쿨다운 타임스탬프
- `_PUBLIC_ENDPOINTS = {'login_view', 'verify_password_api', 'request_password_api', 'static'}` — 로그인 없이 접근 가능한 엔드포인트 화이트리스트

| 함수 / 라우트 | 동작 |
|---|---|
| `require_login()` (`@app.before_request`) | 모든 요청 전에 실행되는 게이트키퍼. `/api/sync/*`(서버 간 동기화, 별도 토큰 인증)는 통과. 잠금이 꺼져 있으면 그냥 통과. 화이트리스트 엔드포인트면 통과. 그 외에 세션에 `logged_in`이 없으면 `/api/*`는 401 JSON, 나머지는 `/login`으로 리다이렉트 |
| `login_view()` (`GET /login`) | 로그인 페이지 렌더링. 이미 로그인돼 있으면 `/`로 바로 보냄 |
| `login_status_api()` (`GET /api/login/status`) | 잠금 on/off 여부만 반환 (비밀번호 자체는 절대 안 내려줌) |
| `_issue_new_password()` | 숫자 7자리 랜덤 생성 → `generate_password_hash`로 해시 후 `_login_state`/DB에 저장 → `send_slack_msg`로 평문 발송. 토글 ON·재발급·"비밀번호 받기" 세 군데에서 공통으로 호출 |
| `toggle_lock_api()` (`POST /api/login/toggle`) | `{enabled: bool}` 받음. **켤 때**는 웹훅 설정 확인 후 `_issue_new_password()` 호출(항상 새 비밀번호, 재발급 포함). **끌 때**는 비밀번호는 그대로 두고 잠금 플래그만 끔(다음에 켤 때 재사용 안 되고 또 새로 발급됨) |
| `reissue_password_api()` (`POST /api/login/reissue`) | 잠금 켜진 채로 비밀번호만 재발급. `require_login()`이 이미 로그인 여부를 보장하므로 별도 인증 체크 없음 |
| `verify_password_api()` (`POST /api/login/verify`) | 비밀번호 검증 → 세션에 `logged_in=True`, `session.permanent=True`(30일). 5회 연속 실패 시 60초 잠금(`_login_locked_until`) |
| `request_password_api()` (`POST /api/login/request`) | 로그인 페이지에서 "비밀번호를 잊었을 때" 재발급 요청. 로그인 없이 호출 가능한 공개 API라 120초 쿨다운을 둠 |
| `logout_view()` (`GET /logout`) | `session.clear()` 후 `/login`으로 |
| `security_view()` (`GET /security`) | 잠금 토글 + 재발급 UI 페이지 렌더링 |

### 5. 템플릿

| 파일 | 역할 |
|---|---|
| `templates/login.html` | 비밀번호 입력 폼 + "비밀번호 받기(Slack)" 버튼. 재요청 쿨다운을 `localStorage`에도 저장해 새로고침해도 풀리지 않게 함 |
| `templates/security.html` | 잠금 on/off 스위치(`/api/login/toggle` 호출) + 비밀번호 재발급 버튼(`/api/login/reissue` 호출) |

## 보안 장치 정리

- **비밀번호는 해시로만 저장** (`werkzeug.security.generate_password_hash` / `check_password_hash`), 평문은 Slack 메시지로만 1회성 노출
- **브루트포스 방지**: 5회 연속 실패 시 60초 동안 검증 API 자체를 429로 막음
- **비밀번호 재요청 남용 방지**: 로그인 페이지의 "비밀번호 받기"는 120초 쿨다운
- **잠금 끄면 전체 오픈**: `require_login()`에서 `lock_enabled`가 False면 그 어떤 경로도 막지 않음 — 즉 보안은 전적으로 "잠금 토글" 상태에 의존
- **서버 간 동기화 경로(`/api/sync/*`)는 세션 로그인과 무관하게 별도 토큰(X-Sync-Token)으로 인증** — 로그인 잠금과 섞이지 않게 분리돼 있음
- **기본값**: DB에 `login_settings` 행이 아예 없는 최초 실행 시 잠금 **켜짐**으로 취급 ([app/utils/db_manager.py](../app/utils/db_manager.py) `get_login_settings()` / 테이블 `DEFAULT 1`) — 배포 직후 곧바로 열려 있는 상태가 되는 걸 방지하기 위한 설정

## 왜 "OTP"가 아니라 "토글+상시 비밀번호"인가

- 예전 버전(`c64dd7d`)은 Slack으로 1회용 코드를 보내 로그인마다 새로 받는 OTP 방식이었음
- 지금 버전(`656b645`)은 잠금을 켤 때만 비밀번호를 발급하고, 끄기 전까지는 같은 비밀번호를 계속 재사용 — 매번 Slack을 확인해야 하는 번거로움을 줄이면서도, 잠금이 켜져 있을 때는 접근을 막을 수 있게 함
- 관련 커밋: `c64dd7d`(Slack OTP 최초 도입) → `656b645`(토글+상시 비밀번호로 전환) → `44609fc`(로그인 페이지에 재발급 버튼 추가)
