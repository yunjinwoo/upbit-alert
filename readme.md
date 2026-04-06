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


## GITHUB - actions
https://github.com/yunjinwoo/upbit-alert/settings/secrets/actions
서버정보 넣기

------------------

   1 sudo apt update
   2 sudo apt install nginx -y

  3단계: Nginx 설정 (도메인과 5000번 포트 연결)
  Nginx 설정 파일을 만들어 도메인 주소와 우리 프로그램을 이어줍니다.

   1 # 새로운 설정 파일 생성
   2 sudo nano /etc/nginx/sites-available/upbit-alert

  아래 내용을 복사해서 붙여넣으세요 (도메인 주소만 본인 것으로 수정).

    1 server {
    2     listen 80;
    3     server_name upbit.내도메인.com; # 1단계에서 설정한 도메인 주소
    4
    5     location / {
    6         proxy_pass http://localhost:5000; # Flask 서버 포트
    7         proxy_set_header Host $host;
    8         proxy_set_header X-Real-IP $remote_addr;
    9         proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
   10     }
   11 }
  (저장: Ctrl+O, Enter / 나가기: Ctrl+X)