from db_manager import save_alert_to_db, init_db
from datetime import datetime

# 1. DB 초기화 (파일이 없으면 생성)
init_db()

print("--- [테스트] 수동 데이터 입력 시작 ---")

# 2. 샘플 데이터 1 (비트코인 가짜 데이터)
save_alert_to_db(
    ticker="KRW-BTC",
    surge_count="3/4",
    m240="4시간봉(*3.5배*)",
    m60="1시간봉(*5.2배*)",
    m30="30분봉(*6.1배*)",
    m15="-",
    daily_info="▲2.5%  거래량: 5,000  거래대금: 450,000,000원",
    url="https://upbit.com/exchange?code=CRIX.UPBIT.KRW-BTC"
)

# 3. 샘플 데이터 2 (이더리움 가짜 데이터)
save_alert_to_db(
    ticker="KRW-ETH",
    surge_count="2/4",
    m240="-",
    m60="1시간봉(*4.2배*)",
    m30="30분봉(*8.5배*)",
    m15="-",
    daily_info="▲1.8%  거래량: 12,000  거래대금: 620,000,000원",
    url="https://upbit.com/exchange?code=CRIX.UPBIT.KRW-ETH"
)

print("✅ 성공적으로 2건의 테스트 데이터를 DB에 넣었습니다!")
print("이제 브라우저에서 http://localhost:5000/alerts 를 새로고침해 보세요.")
