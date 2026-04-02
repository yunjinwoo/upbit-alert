import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import pyupbit
from logger import get_logger

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
            ["시간", "티커", "발화봉수", "타임프레임", "일봉 거래량", "URL"],
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

# save_alert.py
def save_to_sheet(ticker, active_intervals, surge_count, daily_str):  # daily 파라미터 추가
    try:
        sheet = get_sheet()
        sheet.insert_row([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ticker,
            f"{surge_count}/4",
            ", ".join(active_intervals),
            daily_str,  # 바로 사용
            f"https://upbit.com/exchange?code=CRIX.UPBIT.{ticker}"
        ], index=2)

    except Exception as e:
        logger.error(f"[시트 저장 실패] {e}")