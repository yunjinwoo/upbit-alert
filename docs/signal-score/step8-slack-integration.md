# 8단계: 주식 알림 Slack 연동 배선

- 상태: 완료
- 날짜: 2026-07-10
- 관련 파일: `app/core/stock_monitor.py`

## 작업 내용
7번에서 만든 종합 Signal Score를 실제 운영 스케줄에 연결해서 등급별로 다르게 처리되도록 배선.

### 등급별 처리
- **A등급(80점↑)**: 코인용 `send_slack_msg`(`app/core/upbit_monitor.py`)를 재사용해 Slack으로 즉시 알림
- **B등급(65~79)/C등급(50~64)**: 알림 없이 `signal_score_daily`에 저장만 — `get_signal_score_history(grade='B')` 등으로 조회 가능(대시보드 노출은 화면이 필요하면 별도로 추가)
- **제외(50 미만)**: 저장은 되지만 사실상 무시 대상

### 연결 위치
`run_stock_monitor()`의 기존 일 1회 스케줄 블록(오후 3:40, 시총+투자자 데이터 수집 직후)에 이어서 실행:
```python
scores = get_signal_score_batch(fid_input_iscd='combined', save=True)
send_signal_score_alerts(scores)
```
이 블록은 `last_market_cap_date != today_str` 조건으로 하루 1회만 실행되므로 중복 알림 걱정 없음.

### 신규 함수
- `send_signal_score_alerts(scores)` (`stock_monitor.py`) — A등급만 골라 Slack 메시지 발송. 종목명·코드·총점·구성점수 내역·네이버 금융 링크 포함

## 검증
- 실제 Slack 웹훅이 설정되어 있는 상태(`Config.SLACK_WEBHOOK_URL` 존재 확인)라, 실제 발송을 유발하지 않도록 **현재 A등급이 0건인 실제 데이터**로만 검증함 — `send_signal_score_alerts(real_scores)` 실행 시 "A등급 종목 없음" 경로로 안전하게 종료되는 것 확인, 에러 없음
- import 순서/구조, 함수 문법 검증(ast.parse) 통과
- 가짜 A등급 데이터를 만들어 실제 Slack 전송까지 확인하는 것은 실제 채널에 메시지가 발송되는 부작용이 있어 하지 않음 — 7번에서 이미 로직(등급 계산)은 실제 데이터로 충분히 검증됨

## 참고
- 코드 안의 일부 로그 문구(em dash "—" 등)가 특정 콘솔 환경(cp949)에서 `UnicodeEncodeError`를 유발하는 걸 테스트 중 발견했으나, 이는 기존 코드에도 이미 널리 쓰이던 스타일이고 실제 운영 로그(`app.log`, `app.log.1`)에는 이 문제로 크래시한 이력이 없어 별도 수정하지 않음. logging 모듈이 핸들러 오류를 자체적으로 흡수하므로 실행 자체는 계속 진행됨.
- B등급 "대시보드 상단 노출"은 데이터 조회 함수(`get_signal_score_history`)까지만 만들었고, 화면은 아직 없음 — 필요하면 요청 시 추가.

## 다음 단계
9번(선택): 투자자 매매동향 수집 대상을 시총 상위 종목에서 전종목으로 확대 — API 호출량/레이트리밋 고려 필요.
