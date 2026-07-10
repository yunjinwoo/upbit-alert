import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import pyupbit
import os
from app.config import Config
from app.utils.logger import get_logger
from app.utils.db_manager import save_alert_to_db

logger = get_logger()

def get_client():
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
        return gspread.authorize(creds)
    except Exception as e:
        logger.error(f"Failed to connect to Google Sheets: {e}")
        return None

def get_sheet():
    client = get_client()
    if not client:
        return None
    try:
        return client.open(Config.SHEET_NAME).sheet1
    except Exception as e:
        logger.error(f"Failed to connect to Google Sheets: {e}")
        return None

def get_or_create_worksheet(title: str, spreadsheet_title: str = None, rows: int = 200, cols: int = 20):
    """지정한 스프레드시트(기본: Config.SHEET_NAME) 안에서 워크시트 탭을 가져오거나 없으면 새로 만든다.
    스프레드시트 자체는 서비스 계정 소유로 새로 만들 수 없음(서비스 계정은 Drive 저장용량이 0이라
    APIError 403 Drive storage quota exceeded 발생) — 반드시 사용자 계정에서 만들어 서비스 계정
    이메일에 편집자로 공유해둔 스프레드시트여야 한다.
    """
    client = get_client()
    if not client:
        return None
    spreadsheet_title = spreadsheet_title or Config.SHEET_NAME
    try:
        spreadsheet = client.open(spreadsheet_title)
        try:
            return spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)
    except gspread.SpreadsheetNotFound:
        logger.error(
            f"[구글시트] '{spreadsheet_title}'을 찾을 수 없습니다. 사용자 구글 계정에서 이 이름으로 "
            f"스프레드시트를 만들고 서비스 계정({Config.CREDENTIALS_FILE}의 client_email)에 편집자로 "
            f"공유했는지 확인하세요."
        )
        return None
    except Exception as e:
        logger.error(f"Failed to open/create worksheet '{title}' in '{spreadsheet_title}': {e}")
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


def save_signal_score_to_sheet(scores: list, worksheet_title: str = "SignalScore"):
    """Signal Score 배치 결과(+수급 상세)를 NotebookLM 연동 전용 스프레드시트에 통째로 덮어쓴다.
    (Config.NOTEBOOK_SHEET_NAME — 기존 코인 알림용 시트와는 별도 파일)
    이 스프레드시트는 사용자 구글 계정에서 미리 만들어 서비스 계정에 편집자로 공유해둬야 한다.
    scores: get_signal_score_batch()의 반환값 (detail 포함) 그대로 전달.
    """
    worksheet = get_or_create_worksheet(worksheet_title, spreadsheet_title=Config.NOTEBOOK_SHEET_NAME)
    if not worksheet:
        logger.warning("[Signal Score 시트] 워크시트를 열 수 없어 저장을 건너뜁니다.")
        return

    header = [
        "날짜", "종목코드", "종목명", "등급", "총점",
        "모멘텀", "수급점수", "랭킹안정성", "시장환경", "리스크패널티",
        "외국인3일순매수(백만원)", "기관3일순매수(백만원)",
    ]
    rows = [header]
    for s in scores:
        supply = s.get("detail", {}).get("supply_demand", {})
        code = s.get("code", "")
        code_link = f'=HYPERLINK("https://finance.naver.com/item/main.nhn?code={code}", "{code}")' if code else ""
        rows.append([
            s.get("date", ""),
            code_link,
            s.get("name", ""),
            s.get("grade", ""),
            s.get("total", 0),
            s.get("momentum_score", 0),
            s.get("supply_demand_score", 0),
            s.get("rank_stability_score", 0),
            s.get("market_environment_score", 0),
            s.get("risk_penalty_score", 0),
            supply.get("frgn_total", 0),
            supply.get("orgn_total", 0),
        ])

    try:
        worksheet.clear()
        worksheet.update(rows, value_input_option="USER_ENTERED")
        logger.info(f"[Signal Score 시트] {len(scores)}건 저장 완료 (탭: {worksheet_title})")
    except Exception as e:
        logger.error(f"[Signal Score 시트] 저장 실패: {e}")
