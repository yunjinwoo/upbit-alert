import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import pyupbit
import os
from app.config import Config
from app.utils.logger import get_logger
from app.utils.db_manager import save_alert_to_db, get_recent_investor_dates, get_investor_cross_distribution

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
            "주봉", "일봉", "4시간봉",
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

def save_to_sheet(ticker, active_intervals, surge_count, daily_str, total_intervals=None):
    try:
        def get_ratio_str(name):
            for item in active_intervals:
                if name in item:
                    return item
            return "-"

        # 데이터가 없어 평가에서 제외된 타임프레임(예: 상장 초기라 주봉 이력 부족)은 분모에서 빠진 값으로
        # 넘어오므로 그대로 사용 — 안 넘어온 경우(예: 다른 호출부)를 대비해 전체 개수로 폴백
        if total_intervals is None:
            total_intervals = len(Config.UPBIT_INTERVALS)

        # Save to SQLite DB (Always)
        # m60/m30/m15는 더 이상 감시하지 않는 분봉(1시간/30분/15분)의 잔재 컬럼 — 항상 "-"로 저장
        save_alert_to_db(
            ticker,
            f"{surge_count}/{total_intervals}",
            get_ratio_str("4시간봉"),
            "-",
            "-",
            "-",
            daily_str,
            f"https://upbit.com/exchange?code=CRIX.UPBIT.{ticker}",
            mweek=get_ratio_str("주봉"),
            mday=get_ratio_str("일봉"),
        )

        # Save to Google Sheets (If configured)
        sheet = get_sheet()
        if sheet:
            if surge_count >= total_intervals:
                icon = "🔴"
            elif surge_count == total_intervals - 1:
                icon = "🟠"
            else:
                icon = "🟡"

            sheet.insert_row([
                f"{icon} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                ticker,
                f"{surge_count}/{total_intervals}",
                get_ratio_str("주봉"),
                get_ratio_str("일봉"),
                get_ratio_str("4시간봉"),
                daily_str,
                f"https://upbit.com/exchange?code=CRIX.UPBIT.{ticker}"
            ], index=2)

            if surge_count >= total_intervals:
                bg_color = {"red": 1.0, "green": 0.6, "blue": 0.6}
            elif surge_count == total_intervals - 1:
                bg_color = {"red": 1.0, "green": 0.8, "blue": 0.6}
            else:
                bg_color = {"red": 1.0, "green": 1.0, "blue": 0.8}

            sheet.format("A2:F2", {
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
        "날짜", "종목코드", "종목명", "등급", "기본점수", "HTS조회상위보너스", "관심종목등록보너스", "총점",
        "모멘텀", "수급점수", "랭킹안정성", "시장환경", "리스크패널티",
        "외국인3일순매수(백만원)", "기관3일순매수(백만원)",
    ]
    rows = [header]
    for s in scores:
        supply = s.get("detail", {}).get("supply_demand", {})
        code = s.get("code", "")
        code_link = f'=HYPERLINK("https://finance.naver.com/item/main.nhn?code={code}", "{code}")' if code else ""
        hts_bonus = s.get("hts_top_view_bonus_score", 0)
        top_interest_bonus = s.get("top_interest_bonus_score", 0)
        total = s.get("total", 0)
        rows.append([
            s.get("date", ""),
            code_link,
            s.get("name", ""),
            s.get("grade", ""),
            total - hts_bonus - top_interest_bonus,
            hts_bonus,
            top_interest_bonus,
            total,
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


def save_signal_score_readme(worksheet_title: str = "README"):
    """SignalScore 탭의 컬럼 정의와 계산 방식을 설명하는 안내 탭 작성.
    NotebookLM 같은 외부 도구가 같은 스프레드시트 안에서 데이터의 의미를 바로 참고할 수 있도록
    같은 파일(Config.NOTEBOOK_SHEET_NAME)에 문서화해둔다. 내용이 자주 바뀌지 않으므로 앱 시작 시
    1회만 덮어쓴다.
    """
    worksheet = get_or_create_worksheet(worksheet_title, spreadsheet_title=Config.NOTEBOOK_SHEET_NAME, rows=60, cols=2)
    if not worksheet:
        logger.warning("[Signal Score README] 워크시트를 열 수 없어 작성을 건너뜁니다.")
        return

    lines = [
        "Signal Score 데이터 설명",
        "",
        "이 파일의 SignalScore 탭은 한국 주식 종목의 매일 계산되는 Signal Score 스냅샷입니다.",
        "장 마감 후(약 15:40, 평일) 자동 갱신되며, 코스피/코스닥 전체 상장 종목이 아니라 시가총액 상위 종목이 대상입니다.",
        "",
        "[컬럼 설명]",
        "날짜: 데이터 기준일 (YYYY-MM-DD)",
        "종목코드: 클릭하면 네이버 증권 페이지로 이동",
        "종목명: 종목명",
        "등급: A(80점 이상) / B(65~79점) / C(50~64점) / 제외(50점 미만) — 등급은 총점(기본점수+HTS조회상위보너스+관심종목등록보너스) 기준",
        "기본점수(0~105점): 모멘텀+수급점수+랭킹안정성+시장환경+리스크패널티 5개 항목의 합",
        "HTS조회상위보너스(0~20점) + 관심종목등록보너스(0~10점) = 관심도 보너스(합산 후 최대 30점 상한). 기본점수 위에 얹는 추가 점수",
        "총점: 기본점수 + HTS조회상위보너스 + 관심종목등록보너스 (관심도 보너스 합산 30점 상한 적용)",
        "모멘텀(0~30점): 최근 20영업일 평균 거래량 대비 당일 거래량 배수 + 당일 등락률 조합. 거래량이 급증하며 가격도 오를수록 고득점",
        "수급점수(-15~30점): 외국인·기관 3영업일 누적 순매수 조합. 동반 순매수=30점, 한쪽만 순매수=15점, 동반 순매도=-15점",
        "랭킹안정성(0~15점): 시가총액 상위 100위 이내(+10점) + 최근 5영업일간 랭킹 상승(+5점)",
        "시장환경(0~15점): 소속 시장(코스피/코스닥) 지수의 당일 등락(+10점) + 5영업일 상승 추세(+5점)",
        "리스크패널티(-20~15점): 최근 7일 강한 상승 추세(+30%이상 +15점) / 거래량급증+당일하락(-10점) / 외국인·기관 동반순매도(-15점) 합산, 하한 -20점(상한 없음)",
        "HTS조회상위가점(0~20점): 당일 HTS조회상위20종목(실시간 관심도 랭킹)에 등장했는지. 최고 순위 1~3위=20점, 4~10위=12점, 11위 이하=6점, 미등장=0점",
        "관심종목등록가점(0~10점): 당일 관심종목등록 상위(등록 건수 기준 랭킹)에 등장했는지. 최고 순위 1~3위=10점, 4~10위=6점, 11위 이하=3점, 미등장=0점",
        "외국인3일순매수(백만원): 외국인 투자자의 최근 3영업일 누적 순매수 금액",
        "기관3일순매수(백만원): 기관 투자자의 최근 3영업일 누적 순매수 금액",
        "",
        "[알려진 한계]",
        "- 모멘텀 점수 계산에 필요한 20영업일치 거래량 데이터가 아직 충분히 쌓이지 않아 당분간 0점으로 나올 수 있음(데이터 누적 중)",
        "- 외국인/기관 순매수 데이터는 시가총액 상위 종목만 수집됨(전체 상장 종목이 아님)",
        "- SignalScore 탭은 최대 60건(코스피 30 + 코스닥 30)까지만 표시됨 — KIS 시가총액 순위 API가 시장별 최대 30종목까지만 반환하며, 연속조회(tr_cont)·가격 필터 우회 모두 시도했으나 API 자체가 31위 이상을 지원하지 않는 것으로 확인됨(2026-07-13)",
        "- 이 점수는 투자 조언이 아니라 알림 우선순위를 정하기 위한 규칙 기반 참고 지표",
        "",
        "[InvestorRanking 탭]",
        "SignalScore와 별도로, 최근 10영업일 기준 외국인+기관 순매수/순매도 상위 40종목을 보여주는 탭입니다.",
        "SignalScore의 수급점수는 3영업일 누적만 보므로, 이 탭은 그보다 긴 흐름(꾸준한 매집/이탈)을 보는 용도입니다.",
        "기간(최근 10영업일)과 상위 40종목 모두 매번 갱신 시점 기준으로 다시 계산되는 롤링 방식이라, 어제와 오늘 종목 목록이 달라질 수 있습니다.",
        "구분 컬럼: 동반 순매수(외국인·기관 모두 순매수) / 동반 순매도(모두 순매도) / 혼조(한쪽만 순매수)",
    ]
    rows = [[line] for line in lines]

    try:
        worksheet.clear()
        worksheet.update(rows)
        logger.info(f"[Signal Score README] 안내 문서 저장 완료 (탭: {worksheet_title})")
    except Exception as e:
        logger.error(f"[Signal Score README] 저장 실패: {e}")


def save_investor_ranking_to_sheet(days: int = 10, top_n: int = 40, worksheet_title: str = "InvestorRanking"):
    """최근 N영업일 외국인+기관 순매수/순매도 상위 종목을 NotebookLM 연동 스프레드시트에 저장.
    기존 순매수 랭킹 페이지와 동일한 get_investor_cross_distribution()을 재사용 — 날짜 범위는
    stock_investor_daily에 실제로 존재하는 최근 영업일 기준으로 매번 새로 계산되는 롤링 윈도우.
    """
    dates = get_recent_investor_dates(limit=days)
    if not dates:
        logger.warning("[투자자순매수 시트] 투자자매매동향 데이터가 없어 저장을 건너뜁니다.")
        return

    date_to = dates[0]
    date_from = dates[-1]
    rows_data = get_investor_cross_distribution(date_from, date_to, top_n=top_n)

    worksheet = get_or_create_worksheet(worksheet_title, spreadsheet_title=Config.NOTEBOOK_SHEET_NAME)
    if not worksheet:
        logger.warning("[투자자순매수 시트] 워크시트를 열 수 없어 저장을 건너뜁니다.")
        return

    period_str = f"{date_from} ~ {date_to}"
    header = [
        "기간", "종목코드", "종목명",
        "외국인순매수합계(백만원)", "기관순매수합계(백만원)",
        "구분", "외국인거래일수", "기관거래일수",
    ]
    rows = [header]
    for r in rows_data:
        frgn = r.get("frgn_total", 0)
        orgn = r.get("orgn_total", 0)
        if frgn > 0 and orgn > 0:
            direction = "동반 순매수"
        elif frgn < 0 and orgn < 0:
            direction = "동반 순매도"
        else:
            direction = "혼조"

        code = r.get("code", "")
        code_link = f'=HYPERLINK("https://finance.naver.com/item/main.nhn?code={code}", "{code}")' if code else ""
        rows.append([
            period_str, code_link, r.get("name", ""),
            frgn, orgn, direction,
            r.get("frgn_days", 0), r.get("orgn_days", 0),
        ])

    try:
        worksheet.clear()
        worksheet.update(rows, value_input_option="USER_ENTERED")
        logger.info(f"[투자자순매수 시트] {len(rows_data)}건 저장 완료 (탭: {worksheet_title}, 기간: {period_str})")
    except Exception as e:
        logger.error(f"[투자자순매수 시트] 저장 실패: {e}")
