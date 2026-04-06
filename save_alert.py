import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import pyupbit
from logger import get_logger
from db_manager import save_alert_to_db, init_db

# Initialize DB on import
init_db()

logger = get_logger()

SHEET_NAME = "py-upbit-alram"  # 본인 구글 시트 이름으로 변경

def get_sheet():
    creds = Credentials.from_service_account_file(
        "credentials.json",
        scopes=[
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
    )
    client = gspread.authorize(creds)
    return client.open(SHEET_NAME).sheet1


def init_sheet():
    sheet = get_sheet()
    if sheet.cell(1, 1).value != "시간":
        sheet.insert_row(
            ["시간", "티커", "발화봉수",
            "4시간봉", "1시간봉", "30분봉", "15분봉",  # ← 각각 컬럼으로 분리
            "일봉 거래량", "URL"],
            index=1
        )


def get_daily_volume_info(ticker):
    """
    일봉 거래량 정보 반환
    - 오늘 거래량이 전일보다 클 때만 데이터 반환
    - 아니면 None 반환
    """
    try:
        df = pyupbit.get_ohlcv(ticker, interval="day", count=2)  # 오늘 + 전일
        if df is None or len(df) < 2:
            return None

        today    = df.iloc[-1]
        yesterday = df.iloc[-2]

        today_vol     = today["volume"]
        yesterday_vol = yesterday["volume"]

        # 전일보다 거래량이 작으면 None 반환
        if today_vol <= yesterday_vol:
            return None

        increase_rate = ((today_vol - yesterday_vol) / yesterday_vol) * 100
        trade_value   = today["value"]  # 거래대금 (원화)

        return {
            "today_vol"    : int(today_vol),
            "yesterday_vol": int(yesterday_vol),
            "increase_rate": round(increase_rate, 1),
            "trade_value"  : int(trade_value)
        }

    except Exception as e:
        logger.error(f"[일봉 조회 실패] {e}")
        return None



# def save_to_sheet(ticker, active_intervals, surge_count):
#     try:
#         sheet = get_sheet()
#         sheet.insert_row([
#             datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
#             ticker,
#             f"{surge_count}/4",
#             ", ".join(active_intervals),
#             f"https://upbit.com/exchange?code=CRIX.UPBIT.{ticker}"
#         ], index=2)  # 최신이 위로
#     except Exception as e:
#         print(f"[시트 저장 실패] {e}")

# red, green, blue 모두 0.0 ~ 1.0 사이 값
#{"red": 1.0, "green": 0.0, "blue": 0.0}  # 빨강
#{"red": 1.0, "green": 0.5, "blue": 0.0}  # 주황
#{"red": 1.0, "green": 1.0, "blue": 0.0}  # 노랑
#{"red": 0.0, "green": 0.8, "blue": 0.0}  # 초록
#{"red": 1.0, "green": 1.0, "blue": 1.0}  # 흰색
#{"red": 0.9, "green": 0.9, "blue": 0.9}  # 연회색
# save_alert.py
def save_to_sheet(ticker, active_intervals, surge_count, daily_str):  # daily 파라미터 추가
    try:
        # active_intervals 리스트에서 각 타임프레임 값 추출
        def get_ratio_str(name):
            for item in active_intervals:
                if name in item:
                    return item  # 예: "1시간봉(*4.5배* / 기준:4.0배)"
            return "-"  # 조건 미달이면 "-"

        if surge_count >= 4:
            icon = "🔴"
        elif surge_count == 3:
            icon = "🟠"
        else:
            icon = "🟡"

        sheet = get_sheet()
        sheet.insert_row([
            f"{icon} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            ticker,
            f"{surge_count}/4",
            get_ratio_str("4시간봉"),  # 4시간봉 컬럼
            get_ratio_str("1시간봉"),  # 1시간봉 컬럼
            get_ratio_str("30분봉"),   # 30분봉 컬럼
            get_ratio_str("15분봉"),   # 15분봉 컬럼
            daily_str,  # 바로 사용
            f"https://upbit.com/exchange?code=CRIX.UPBIT.{ticker}"
        ], index=2)

        # 색상 조건 분기
        if surge_count >= 4:
            bg_color = {"red": 1.0, "green": 0.6, "blue": 0.6}  # 🔴 빨강 - 전부 폭발
        elif surge_count == 3:
            bg_color = {"red": 1.0, "green": 0.8, "blue": 0.6}  # 🟠 주황
        else:
            bg_color = {"red": 1.0, "green": 1.0, "blue": 0.8}  # 🟡 노랑

        sheet.format("A2:D2", {
            "backgroundColor": bg_color,
            "textFormat": {"bold": True}
        })
        
        # Save to SQLite DB for API access
        save_alert_to_db(
            ticker, 
            f"{surge_count}/4", 
            get_ratio_str("4시간봉"), 
            get_ratio_str("1시간봉"), 
            get_ratio_str("30분봉"), 
            get_ratio_str("15분봉"), 
            daily_str, 
            f"https://upbit.com/exchange?code=CRIX.UPBIT.{ticker}"
        )
    except Exception as e:
        logger.error(f"[시트 저장 실패] {e}")