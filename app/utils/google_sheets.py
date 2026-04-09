import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import pyupbit
import os
from app.config import Config
from app.utils.logger import get_logger
from app.utils.db_manager import save_alert_to_db

logger = get_logger()

def get_sheet():
    if not os.path.exists(Config.CREDENTIALS_FILE):
        logger.warning(f"Credentials file {Config.CREDENTIALS_FILE} not found. Google Sheets integration disabled.")
        return None
    try:
        creds = Credentials.from_service_account_file(
            Config.CREDENTIALS_FILE,
            scopes=[
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"
            ]
        )
        client = gspread.authorize(creds)
        return client.open(Config.SHEET_NAME).sheet1
    except Exception as e:
        logger.error(f"Failed to connect to Google Sheets: {e}")
        return None

def init_sheet():
    sheet = get_sheet()
    if sheet and sheet.cell(1, 1).value != "시간":
        sheet.insert_row(
            ["시간", "티커", "발화봉수",
            "4시간봉", "1시간봉", "30분봉", "15분봉",
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
        df = pyupbit.get_ohlcv(ticker, interval="day", count=2)
        if df is None or len(df) < 2:
            return None

        today = df.iloc[-1]
        yesterday = df.iloc[-2]

        today_vol = today["volume"]
        yesterday_vol = yesterday["volume"]

        if today_vol <= yesterday_vol:
            return None

        increase_rate = ((today_vol - yesterday_vol) / yesterday_vol) * 100
        trade_value = today["value"]

        return {
            "today_vol": int(today_vol),
            "yesterday_vol": int(yesterday_vol),
            "increase_rate": round(increase_rate, 1),
            "trade_value": int(trade_value)
        }

    except Exception as e:
        logger.error(f"[일봉 조회 실패] {e}")
        return None

def save_to_sheet(ticker, active_intervals, surge_count, daily_str):
    try:
        def get_ratio_str(name):
            for item in active_intervals:
                if name in item:
                    return item
            return "-"

        # Save to SQLite DB (Always)
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

        # Save to Google Sheets (If configured)
        sheet = get_sheet()
        if sheet:
            if surge_count >= 4:
                icon = "🔴"
            elif surge_count == 3:
                icon = "🟠"
            else:
                icon = "🟡"

            sheet.insert_row([
                f"{icon} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                ticker,
                f"{surge_count}/4",
                get_ratio_str("4시간봉"),
                get_ratio_str("1시간봉"),
                get_ratio_str("30분봉"),
                get_ratio_str("15분봉"),
                daily_str,
                f"https://upbit.com/exchange?code=CRIX.UPBIT.{ticker}"
            ], index=2)

            if surge_count >= 4:
                bg_color = {"red": 1.0, "green": 0.6, "blue": 0.6}
            elif surge_count == 3:
                bg_color = {"red": 1.0, "green": 0.8, "blue": 0.6}
            else:
                bg_color = {"red": 1.0, "green": 1.0, "blue": 0.8}

            sheet.format("A2:D2", {
                "backgroundColor": bg_color,
                "textFormat": {"bold": True}
            })
            
    except Exception as e:
        logger.error(f"[저장 실패] {e}")
