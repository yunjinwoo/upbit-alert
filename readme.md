설치한 플러그인.

# pip install pyupbit requests
#### pip install --upgrade pandas pyupbit requests
# pip install ccxt pandas

# pip install gspread google-auth
# pip install python-dotenv

## .env 에 키 넣기 - 슬랙
## credentials.json 에 값넣기 - 구글시트

https://api.slack.com/apps 슬랙
### 1. 사전 준비: 슬랙 웹훅(Webhook) URL 발급
슬랙으로 알림을 보내려면 'Incoming Webhooks' 앱을 추가하여 Webhook URL을 생성해야 합니다.
 - Slack API 페이지에서 앱 생성
 - Incoming Webhooks 활성화
 - 알림을 받을 채널을 선택하고 Add New Webhook to Workspace 클릭

생성된 https://hooks.slack.com/services/... 형태의 URL을 복사해 두세요.


### 2. 1️⃣ 준비 (최초 1회만) 구글시트

① Google Cloud Console에서 설정

console.cloud.google.com 접속
새 프로젝트 생성
Google Sheets API + Google Drive API 활성화
서비스 계정 생성 → JSON 키 다운로드 → credentials.json으로 저장

② 패키지 설치
bashpip install gspread google-auth
③ 스프레드시트 공유 설정

구글 시트 새로 만들기
credentials.json 안의 client_email 값을 시트에 편집자로 공유

## 내보내기
pip freeze > requirements.txt
## 가져오기
pip install -r requirements.txt