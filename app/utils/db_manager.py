import sqlite3
from datetime import datetime, timedelta
import os
import json
from typing import List
from app.config import Config
from app.core.kis_models import MarketCapRankingItem, StockInvestorDailyItem

DB_PATH = Config.DB_NAME

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # 코인 알림 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            ticker TEXT,
            surge_count TEXT,
            m240 TEXT,
            m60 TEXT,
            m30 TEXT,
            m15 TEXT,
            daily_info TEXT,
            url TEXT
        )
    ''')
    # 주식 알림 테이블 (신규)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            code TEXT,
            name TEXT,
            price TEXT,
            change_rate TEXT,
            volume TEXT,
            volume_power TEXT,
            market_cap TEXT,
            reason TEXT,
            url TEXT
        )
    ''')
    # API 토큰 저장 테이블 (신규)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS api_tokens (
            provider TEXT PRIMARY KEY,
            token TEXT,
            issued_date TEXT
        )
    ''')


    # 골든 데이터셋 저장소 만들기 - 하네스 테스트용
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gold_dataset (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scenario_name TEXT,
            input_data TEXT,       -- 업비트 Mock 데이터 (JSON)
            expected_output TEXT,  -- 기대하는 슬랙 메시지
            category TEXT          -- '폭등', '횡보', '에러처리' 등
        )
    ''')

    # 주식 원본 데이터 저장 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_raw_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            api_type TEXT,
            raw_json TEXT
        )
    ''')

    # 일별 시가총액 데이터 저장 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_market_cap_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            code TEXT,
            name TEXT,
            market_cap_amount TEXT,
            rank INTEGER,
            price TEXT,
            change_rate TEXT,
            market_weight TEXT,
            fid_input_iscd TEXT,
            volume TEXT,
            timestamp TEXT
        )
    ''')

    # 일별 투자자별 프로그램 매매동향 저장 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS investor_trend_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            exch_div TEXT,
            mrkt_div TEXT,
            invr_cls_code TEXT,
            invr_cls_name TEXT,
            all_shnu_amt TEXT,
            all_seln_amt TEXT,
            all_ntby_amt TEXT,
            all_shnu_qty TEXT,
            all_seln_qty TEXT,
            all_ntby_qty TEXT,
            arbt_shnu_amt TEXT,
            arbt_seln_amt TEXT,
            arbt_ntby_amt TEXT,
            arbt_shnu_qty TEXT,
            arbt_seln_qty TEXT,
            arbt_ntby_qty TEXT,
            nabt_shnu_amt TEXT,
            nabt_seln_amt TEXT,
            nabt_ntby_amt TEXT,
            nabt_shnu_qty TEXT,
            nabt_seln_qty TEXT,
            nabt_ntby_qty TEXT,
            timestamp TEXT
        )
    ''')

    # 종목별 투자자매매동향(일별) 저장 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_investor_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            code TEXT,
            name TEXT,
            frgn_ntby_qty TEXT,
            frgn_ntby_tr_pbmn TEXT,
            prsn_ntby_qty TEXT,
            prsn_ntby_tr_pbmn TEXT,
            orgn_ntby_qty TEXT,
            orgn_ntby_tr_pbmn TEXT,
            timestamp TEXT,
            UNIQUE(date, code)
        )
    ''')

    # 업종 일자별지수 캐시 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sector_index_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            sector_code TEXT,
            sector_name TEXT,
            close TEXT, open TEXT, high TEXT, low TEXT,
            change TEXT, change_sign TEXT, change_rate TEXT,
            volume TEXT, trade_amount TEXT, vol_ratio TEXT,
            psychology_index TEXT, d20_dsrt TEXT,
            timestamp TEXT,
            UNIQUE(date, sector_code)
        )
    ''')

    # 업종 소속 종목 (일별 스냅샷)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sector_stocks_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            sector_code TEXT,
            sector_name TEXT,
            rank TEXT,
            code TEXT,
            name TEXT,
            price TEXT, change TEXT, change_sign TEXT, change_rate TEXT,
            volume TEXT,
            timestamp TEXT,
            UNIQUE(date, sector_code, code)
        )
    ''')

    # 종목 메모 (전 페이지 공용 — 종목코드당 여러 개 입력 가능한 로그형)
    # 구버전(종목당 1개, code가 PRIMARY KEY)이 남아있으면 새 스키마로 전환하고 기존 메모는
    # 첫 로그 항목으로 그대로 이전한다.
    cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='stock_memo'")
    row = cursor.fetchone()
    if row and 'code TEXT PRIMARY KEY' in row[0]:
        cursor.execute("ALTER TABLE stock_memo RENAME TO stock_memo_old")
        cursor.execute('''
            CREATE TABLE stock_memo (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                name TEXT,
                memo TEXT,
                created_at TEXT
            )
        ''')
        cursor.execute('''
            INSERT INTO stock_memo (code, name, memo, created_at)
            SELECT code, name, memo, updated_at FROM stock_memo_old WHERE memo IS NOT NULL AND memo != ''
        ''')
        cursor.execute("DROP TABLE stock_memo_old")
    else:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stock_memo (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                name TEXT,
                memo TEXT,
                created_at TEXT
            )
        ''')

    # Signal Score 일별 결과 저장 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS signal_score_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            code TEXT,
            name TEXT,
            momentum_score INTEGER,
            supply_demand_score INTEGER,
            rank_stability_score INTEGER,
            market_environment_score INTEGER,
            risk_penalty_score INTEGER,
            hts_top_view_bonus_score INTEGER,
            top_interest_bonus_score INTEGER,
            total_score INTEGER,
            grade TEXT,
            timestamp TEXT,
            UNIQUE(date, code)
        )
    ''')

    # HTS조회상위20종목 시간별 저장 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_hts_top_view_hourly (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            hour INTEGER,
            rank INTEGER,
            code TEXT,
            market_div TEXT,
            name TEXT,
            price TEXT,
            change_rate TEXT,
            prdy_vrss TEXT,
            timestamp TEXT,
            UNIQUE(date, hour, code)
        )
    ''')

    # 관심종목등록 상위 일별 저장 테이블 (네이버 인기검색종목 대체 — robots.txt로 크롤링 불가해서 KIS 공식 API 사용)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_top_interest_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            rank INTEGER,
            code TEXT,
            name TEXT,
            market_div TEXT,
            price TEXT,
            change_rate TEXT,
            reg_count TEXT,
            timestamp TEXT,
            UNIQUE(date, code)
        )
    ''')

    # 스케줄링 작업 실행 이력 (동기화 관리 페이지 "처리 로그"용)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS job_run_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_name TEXT,
            description TEXT,
            api_used TEXT,
            start_time TEXT,
            end_time TEXT,
            duration_sec REAL,
            success INTEGER,
            count INTEGER,
            error_message TEXT,
            trigger_type TEXT
        )
    ''')

    # 기존 테이블 컬럼 마이그레이션
    migrations = [
        'ALTER TABLE stock_market_cap_daily ADD COLUMN market_weight TEXT DEFAULT "0"',
        'ALTER TABLE stock_market_cap_daily ADD COLUMN fid_input_iscd TEXT DEFAULT "0000"',
        'ALTER TABLE stock_market_cap_daily ADD COLUMN volume TEXT DEFAULT "0"',
        'ALTER TABLE stock_raw_data ADD COLUMN api_type TEXT DEFAULT "UNKNOWN"',
        'CREATE UNIQUE INDEX IF NOT EXISTS idx_mktcap_unique ON stock_market_cap_daily(date, code, fid_input_iscd)',
        'CREATE UNIQUE INDEX IF NOT EXISTS idx_invtrend_unique ON investor_trend_daily(date, exch_div, mrkt_div, invr_cls_code)',
        'ALTER TABLE stock_hts_top_view_hourly ADD COLUMN prdy_vrss TEXT',
        'ALTER TABLE signal_score_daily ADD COLUMN hts_top_view_bonus_score INTEGER DEFAULT 0',
        'ALTER TABLE signal_score_daily ADD COLUMN top_interest_bonus_score INTEGER DEFAULT 0',
        # net_buy는 이름과 달리 실제로는 KIS API의 invt_new_psdg(투자 신 심리도) 필드였음 — 이름 정정
        'ALTER TABLE sector_index_daily RENAME COLUMN net_buy TO psychology_index',
    ]
    for sql in migrations:
        try:
            cursor.execute(sql)
        except Exception:
            pass

    conn.commit()
    conn.close()

def save_api_token(provider, token):
    """API 토큰 저장 (날짜 포함)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute('''
        INSERT OR REPLACE INTO api_tokens (provider, token, issued_date)
        VALUES (?, ?, ?)
    ''', (provider, token, today))
    conn.commit()
    conn.close()

def get_api_token(provider):
    """오늘 날짜의 유효한 토큰 조회"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute('''
        SELECT token FROM api_tokens
        WHERE provider = ? AND issued_date = ?
    ''', (provider, today))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def save_alert_to_db(ticker, surge_count, m240, m60, m30, m15, daily_info, url):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO alerts (timestamp, ticker, surge_count, m240, m60, m30, m15, daily_info, url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (timestamp, ticker, surge_count, m240, m60, m30, m15, daily_info, url))
    conn.commit()
    conn.close()

def save_stock_alert_to_db(code, name, price, change_rate, volume, volume_power, market_cap, reason, url):
    """주식 알림 저장"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO stock_alerts (timestamp, code, name, price, change_rate, volume, volume_power, market_cap, reason, url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (timestamp, code, name, price, change_rate, volume, volume_power, market_cap, reason, url))
    conn.commit()
    conn.close()

def get_latest_alerts(limit=100):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM alerts ORDER BY id DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]

def get_latest_stock_alerts(limit=100):
    """최신 주식 알림 조회"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM stock_alerts ORDER BY id DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]

def get_today_alert_count(ticker):
    """오늘 해당 티커의 알림 횟수 조회"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute('''
        SELECT COUNT(*) FROM alerts
        WHERE ticker = ? AND timestamp LIKE ?
    ''', (ticker, f"{today}%"))
    count = cursor.fetchone()[0]
    conn.close()
    return count

def delete_alert(alert_id):
    """특정 ID의 코인 알림을 삭제합니다."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM alerts WHERE id = ?', (alert_id,))
    conn.commit()
    conn.close()

def delete_stock_alert(alert_id):
    """특정 ID의 주식 알림을 삭제합니다."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM stock_alerts WHERE id = ?', (alert_id,))
    conn.commit()
    conn.close()

def save_stock_raw_data(data, api_type="UNKNOWN"):
    """주식 원본 데이터 저장"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO stock_raw_data (timestamp, api_type, raw_json)
        VALUES (?, ?, ?)
    ''', (timestamp, api_type, json.dumps(data, ensure_ascii=False)))
    conn.commit()
    conn.close()

def get_latest_stock_raw_data(limit=10):
    """최신 주식 원본 데이터 조회"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM stock_raw_data ORDER BY id DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]

def save_daily_market_cap(data_list: List[MarketCapRankingItem], fid_input_iscd: str = "0000"):
    """일별 시가총액 순위 데이터 일괄 저장 (당일 + 동일 시장구분 데이터 덮어쓰기)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    today_date = datetime.now().strftime('%Y-%m-%d')
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute('DELETE FROM stock_market_cap_daily WHERE date = ? AND fid_input_iscd = ?', (today_date, fid_input_iscd))

    insert_data = []
    for item in data_list:
        insert_data.append((
            today_date,
            item.mksc_shrn_iscd,
            item.hts_kor_isnm,
            item.stck_avls,
            int(item.data_rank),
            item.stck_prpr,
            item.prdy_ctrt,
            item.mrkt_whol_avls_rlim,
            fid_input_iscd,
            item.acml_vol,
            timestamp
        ))

    cursor.executemany('''
        INSERT INTO stock_market_cap_daily (date, code, name, market_cap_amount, rank, price, change_rate, market_weight, fid_input_iscd, volume, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', insert_data)

    conn.commit()
    conn.close()

def get_market_cap_history(code=None, limit_dates=7, fid_input_iscd="combined", date=None):
    """시총 순위 이력 조회. fid_input_iscd='combined' 이면 거래소+코스닥 합산."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if fid_input_iscd == "combined":
        iscd_list = ("0001", "1001")
    else:
        iscd_list = (fid_input_iscd,)

    placeholders = ",".join("?" * len(iscd_list))

    if code:
        cursor.execute(f'''
            SELECT * FROM stock_market_cap_daily
            WHERE code = ? AND fid_input_iscd IN ({placeholders})
            ORDER BY date ASC
        ''', (code, *iscd_list))
    elif date:
        cursor.execute(f'''
            SELECT * FROM stock_market_cap_daily
            WHERE date = ? AND fid_input_iscd IN ({placeholders})
            ORDER BY CAST(market_cap_amount AS INTEGER) DESC
        ''', (date, *iscd_list))
    else:
        cursor.execute(f'''
            SELECT * FROM stock_market_cap_daily
            WHERE fid_input_iscd IN ({placeholders})
              AND date IN (
                  SELECT DISTINCT date FROM stock_market_cap_daily
                  WHERE fid_input_iscd IN ({placeholders})
                  ORDER BY date DESC LIMIT ?
              )
            ORDER BY date DESC, CAST(market_cap_amount AS INTEGER) DESC
        ''', (*iscd_list, *iscd_list, limit_dates))

    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def save_daily_investor_trend(output1: list, exch_div: str, mrkt_div: str):
    """투자자별 프로그램 매매동향 일별 저장 (당일 데이터 덮어쓰기)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute(
        'DELETE FROM investor_trend_daily WHERE date = ? AND exch_div = ? AND mrkt_div = ?',
        (today, exch_div, mrkt_div)
    )

    rows = []
    for item in output1:
        rows.append((
            today, exch_div, mrkt_div,
            item.get('invr_cls_code', ''),
            item.get('invr_cls_name', ''),
            item.get('all_shnu_amt', '0'),
            item.get('all_seln_amt', '0'),
            item.get('all_ntby_amt', '0'),
            item.get('all_shnu_qty', '0'),
            item.get('all_seln_qty', '0'),
            item.get('all_ntby_qty', '0'),
            item.get('arbt_shnu_amt', '0'),
            item.get('arbt_seln_amt', '0'),
            item.get('arbt_ntby_amt', '0'),
            item.get('arbt_shnu_qty', '0'),
            item.get('arbt_seln_qty', '0'),
            item.get('arbt_ntby_qty', '0'),
            item.get('nabt_shnu_amt', '0'),
            item.get('nabt_seln_amt', '0'),
            item.get('nabt_ntby_amt', '0'),
            item.get('nabt_shnu_qty', '0'),
            item.get('nabt_seln_qty', '0'),
            item.get('nabt_ntby_qty', '0'),
            timestamp,
        ))

    cursor.executemany('''
        INSERT INTO investor_trend_daily (
            date, exch_div, mrkt_div,
            invr_cls_code, invr_cls_name,
            all_shnu_amt, all_seln_amt, all_ntby_amt,
            all_shnu_qty, all_seln_qty, all_ntby_qty,
            arbt_shnu_amt, arbt_seln_amt, arbt_ntby_amt,
            arbt_shnu_qty, arbt_seln_qty, arbt_ntby_qty,
            nabt_shnu_amt, nabt_seln_amt, nabt_ntby_amt,
            nabt_shnu_qty, nabt_seln_qty, nabt_ntby_qty,
            timestamp
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', rows)

    conn.commit()
    conn.close()


def get_investor_trend_history(exch_div: str, mrkt_div: str, limit_days: int = 30):
    """투자자별 프로그램 매매동향 이력 조회"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM investor_trend_daily
        WHERE exch_div = ? AND mrkt_div = ?
          AND date IN (
              SELECT DISTINCT date FROM investor_trend_daily
              WHERE exch_div = ? AND mrkt_div = ?
              ORDER BY date DESC LIMIT ?
          )
        ORDER BY date DESC, invr_cls_code ASC
    ''', (exch_div, mrkt_div, exch_div, mrkt_div, limit_days))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def save_stock_investor_daily(code: str, name: str, items: list):
    """종목별 투자자매매동향 일별 저장 (날짜별 upsert)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for item in items:
        date = item.stck_bsop_date[:4] + '-' + item.stck_bsop_date[4:6] + '-' + item.stck_bsop_date[6:]
        cursor.execute('''
            INSERT INTO stock_investor_daily
                (date, code, name, frgn_ntby_qty, frgn_ntby_tr_pbmn,
                 prsn_ntby_qty, prsn_ntby_tr_pbmn, orgn_ntby_qty, orgn_ntby_tr_pbmn, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, code) DO UPDATE SET
                frgn_ntby_qty=excluded.frgn_ntby_qty,
                frgn_ntby_tr_pbmn=excluded.frgn_ntby_tr_pbmn,
                prsn_ntby_qty=excluded.prsn_ntby_qty,
                prsn_ntby_tr_pbmn=excluded.prsn_ntby_tr_pbmn,
                orgn_ntby_qty=excluded.orgn_ntby_qty,
                orgn_ntby_tr_pbmn=excluded.orgn_ntby_tr_pbmn,
                timestamp=excluded.timestamp
        ''', (date, code, name,
              item.frgn_ntby_qty, item.frgn_ntby_tr_pbmn,
              item.prsn_ntby_qty, item.prsn_ntby_tr_pbmn,
              item.orgn_ntby_qty, item.orgn_ntby_tr_pbmn,
              timestamp))
    conn.commit()
    conn.close()


def get_stock_investor_combined(date: str, fid_input_iscd: str = "combined"):
    """시총 순위 + 투자자 순매수 합산 조회 (특정 날짜)"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if fid_input_iscd == "combined":
        iscd_list = ("0001", "1001")
    else:
        iscd_list = (fid_input_iscd,)

    placeholders = ",".join("?" * len(iscd_list))

    cursor.execute(f'''
        SELECT
            m.date, m.code, m.name, m.rank, m.price, m.change_rate,
            m.market_cap_amount, m.market_weight, m.fid_input_iscd,
            COALESCE(i.frgn_ntby_qty, '0')       AS frgn_ntby_qty,
            COALESCE(i.frgn_ntby_tr_pbmn, '0')   AS frgn_ntby_tr_pbmn,
            COALESCE(i.prsn_ntby_qty, '0')        AS prsn_ntby_qty,
            COALESCE(i.prsn_ntby_tr_pbmn, '0')   AS prsn_ntby_tr_pbmn,
            COALESCE(i.orgn_ntby_qty, '0')        AS orgn_ntby_qty,
            COALESCE(i.orgn_ntby_tr_pbmn, '0')   AS orgn_ntby_tr_pbmn
        FROM stock_market_cap_daily m
        LEFT JOIN stock_investor_daily i ON m.date = i.date AND m.code = i.code
        WHERE m.date = ? AND m.fid_input_iscd IN ({placeholders})
        ORDER BY m.rank ASC
    ''', (date, *iscd_list))

    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_stock_investor_trend(code: str):
    """특정 종목의 날짜별 투자자 순매수 + 시총 이력 조회"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT
            i.date, i.code, i.name,
            i.frgn_ntby_qty, i.frgn_ntby_tr_pbmn,
            i.prsn_ntby_qty, i.prsn_ntby_tr_pbmn,
            i.orgn_ntby_qty, i.orgn_ntby_tr_pbmn,
            COALESCE(m.rank, 0)               AS rank,
            COALESCE(m.market_cap_amount, '0') AS market_cap_amount,
            COALESCE(m.price, '0')            AS price,
            COALESCE(m.change_rate, '0')      AS change_rate
        FROM stock_investor_daily i
        LEFT JOIN (
            SELECT date, code, rank, market_cap_amount, price, change_rate
            FROM stock_market_cap_daily
            GROUP BY date, code
        ) m ON i.date = m.date AND i.code = m.code
        WHERE i.code = ?
        ORDER BY i.date ASC
    ''', (code,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_latest_market_cap_date(fid_input_iscd: str = "combined") -> str:
    """가장 최근 시총 데이터 날짜 반환"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if fid_input_iscd == "combined":
        iscd_list = ("0001", "1001")
    else:
        iscd_list = (fid_input_iscd,)
    placeholders = ",".join("?" * len(iscd_list))
    cursor.execute(f'''
        SELECT MAX(date) FROM stock_market_cap_daily
        WHERE fid_input_iscd IN ({placeholders})
    ''', iscd_list)
    row = cursor.fetchone()
    conn.close()
    return row[0] if row and row[0] else ""


def save_hts_top_view(items: list, date: str, hour: int):
    """HTS조회상위20종목 시간별 스냅샷 저장 (같은 date+hour 데이터는 덮어쓰기).
    items: [{rank, code, market_div, name, price, change_rate, prdy_vrss}, ...]
    prdy_vrss(전일대비, 부호 포함)는 전일종가 = price - prdy_vrss 로 프론트에서 역산해 참고용으로 보여주는 데 쓰인다.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute('DELETE FROM stock_hts_top_view_hourly WHERE date = ? AND hour = ?', (date, hour))

    insert_data = [
        (date, hour, it['rank'], it['code'], it['market_div'], it.get('name'), it.get('price'), it.get('change_rate'),
         it.get('prdy_vrss'), timestamp)
        for it in items
    ]
    cursor.executemany('''
        INSERT INTO stock_hts_top_view_hourly (date, hour, rank, code, market_div, name, price, change_rate, prdy_vrss, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', insert_data)

    conn.commit()
    conn.close()


def get_hts_top_view_history(date: str = None, limit_snapshots: int = 24):
    """HTS조회상위20종목 이력 조회. date 지정 시 해당 날짜 전체, 미지정 시 최근 limit_snapshots개 (date,hour) 스냅샷."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if date:
        cursor.execute('''
            SELECT * FROM stock_hts_top_view_hourly
            WHERE date = ?
            ORDER BY hour DESC, rank ASC
        ''', (date,))
    else:
        cursor.execute('''
            SELECT * FROM stock_hts_top_view_hourly
            WHERE (date, hour) IN (
                SELECT DISTINCT date, hour FROM stock_hts_top_view_hourly
                ORDER BY date DESC, hour DESC LIMIT ?
            )
            ORDER BY date DESC, hour DESC, rank ASC
        ''', (limit_snapshots,))

    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_hts_top_view_export(limit_days: int = 7) -> list:
    """동기화 전송용 — 최근 N일치 HTS조회상위 원본 스냅샷 전체 반환 (해당 기간 내 모든 시간대 포함)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=limit_days)).strftime('%Y-%m-%d')
    cursor.execute('''
        SELECT date, hour, rank, code, market_div, name, price, change_rate, prdy_vrss, timestamp
        FROM stock_hts_top_view_hourly
        WHERE date >= ?
        ORDER BY date DESC, hour DESC, rank ASC
    ''', (cutoff,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def sync_upsert_hts_top_view(rows: list) -> int:
    """stock_hts_top_view_hourly upsert (date+hour+code 기준) — 원격 동기화 수신용"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    saved = 0
    for r in rows:
        cursor.execute('''
            INSERT INTO stock_hts_top_view_hourly
                (date, hour, rank, code, market_div, name, price, change_rate, prdy_vrss, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, hour, code) DO UPDATE SET
                rank=excluded.rank, market_div=excluded.market_div, name=excluded.name,
                price=excluded.price, change_rate=excluded.change_rate, prdy_vrss=excluded.prdy_vrss,
                timestamp=excluded.timestamp
        ''', (r.get('date'), r.get('hour'), r.get('rank'), r.get('code'), r.get('market_div'),
              r.get('name'), r.get('price'), r.get('change_rate'), r.get('prdy_vrss'),
              r.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def save_top_interest_daily(items: list, date: str):
    """관심종목등록 상위 일별 스냅샷 저장 (같은 date 데이터는 덮어쓰기).
    items: [{rank, code, name, market_div, price, change_rate, reg_count}, ...]
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute('DELETE FROM stock_top_interest_daily WHERE date = ?', (date,))

    insert_data = [
        (date, it['rank'], it['code'], it.get('name'), it.get('market_div'),
         it.get('price'), it.get('change_rate'), it.get('reg_count'), timestamp)
        for it in items
    ]
    cursor.executemany('''
        INSERT INTO stock_top_interest_daily (date, rank, code, name, market_div, price, change_rate, reg_count, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', insert_data)

    conn.commit()
    conn.close()


def get_top_interest_range(date_from: str, date_to: str) -> list:
    """관심종목등록 상위 구간 조회 (date_from~date_to 포함, 날짜별 1스냅샷씩).
    반환: [{date, rank, code, name, market_div, price, change_rate, reg_count, timestamp}, ...]
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM stock_top_interest_daily
        WHERE date BETWEEN ? AND ?
        ORDER BY date DESC, rank ASC
    ''', (date_from, date_to))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_top_interest_export(limit_days: int = 7) -> list:
    """동기화 전송용 — 최근 N일치 관심종목등록 상위 원본 스냅샷 전체 반환."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=limit_days)).strftime('%Y-%m-%d')
    cursor.execute('''
        SELECT date, rank, code, name, market_div, price, change_rate, reg_count, timestamp
        FROM stock_top_interest_daily
        WHERE date >= ?
        ORDER BY date DESC, rank ASC
    ''', (cutoff,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def sync_upsert_top_interest(rows: list) -> int:
    """stock_top_interest_daily upsert (date+code 기준) — 원격 동기화 수신용"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    saved = 0
    for r in rows:
        cursor.execute('''
            INSERT INTO stock_top_interest_daily
                (date, rank, code, name, market_div, price, change_rate, reg_count, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, code) DO UPDATE SET
                rank=excluded.rank, name=excluded.name, market_div=excluded.market_div,
                price=excluded.price, change_rate=excluded.change_rate, reg_count=excluded.reg_count,
                timestamp=excluded.timestamp
        ''', (r.get('date'), r.get('rank'), r.get('code'), r.get('name'), r.get('market_div'),
              r.get('price'), r.get('change_rate'), r.get('reg_count'),
              r.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def get_hts_top_view_cumulative(date_from: str, date_to: str):
    """HTS조회상위20종목 구간 누적 점수 조회 (date_from~date_to 포함).
    스냅샷마다 순위 기준 (20 - rank)점을 부여해 종목별로 합산 — 여러 시간대에 걸쳐
    꾸준히 상위권에 머문 종목일수록 높은 점수. 점수 내림차순 정렬.
    반환: [{code, market_div, name, score, appearances, best_rank}, ...]
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT code,
               MAX(market_div) AS market_div,
               MAX(name) AS name,
               SUM(20 - rank) AS score,
               COUNT(*) AS appearances,
               MIN(rank) AS best_rank
        FROM stock_hts_top_view_hourly
        WHERE date BETWEEN ? AND ?
        GROUP BY code
        ORDER BY score DESC
    ''', (date_from, date_to))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_hts_top_view_daily_scores(date_from: str, date_to: str):
    """HTS조회상위20종목 구간 내 날짜별 합산 점수 조회 (date_from~date_to 포함).
    get_hts_top_view_cumulative와 같은 (20-rank) 점수 방식이지만, 구간 전체를 하나로 합치지 않고
    날짜 단위로 따로 집계한다 — 날짜별 순위 매트릭스를 만들 때 사용.
    반환: [{code, market_div, name, date, score, appearances}, ...] (날짜 오름차순, 날짜 내 점수 내림차순)
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT code,
               date,
               MAX(market_div) AS market_div,
               MAX(name) AS name,
               SUM(20 - rank) AS score,
               COUNT(*) AS appearances
        FROM stock_hts_top_view_hourly
        WHERE date BETWEEN ? AND ?
        GROUP BY code, date
        ORDER BY date ASC, score DESC
    ''', (date_from, date_to))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def save_job_run_log(job_name: str, description: str, api_used: str, start_time: str, end_time: str,
                      success: bool, count: int = None, error_message: str = None, trigger_type: str = 'auto'):
    """스케줄링 작업(자동/수동) 실행 결과 1건 기록.
    start_time/end_time: '%Y-%m-%d %H:%M:%S' 형식 문자열.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    duration_sec = None
    try:
        fmt = '%Y-%m-%d %H:%M:%S'
        duration_sec = (datetime.strptime(end_time, fmt) - datetime.strptime(start_time, fmt)).total_seconds()
    except (ValueError, TypeError):
        pass

    cursor.execute('''
        INSERT INTO job_run_log
            (job_name, description, api_used, start_time, end_time, duration_sec, success, count, error_message, trigger_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (job_name, description, api_used, start_time, end_time, duration_sec,
          1 if success else 0, count, error_message, trigger_type))

    conn.commit()
    conn.close()


def get_job_run_log(days: int = 7, job_name: str = None, limit: int = 500) -> list:
    """최근 N일치 작업 실행 이력 조회 (최신순)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    if job_name:
        cursor.execute('''
            SELECT * FROM job_run_log
            WHERE start_time >= ? AND job_name = ?
            ORDER BY start_time DESC LIMIT ?
        ''', (since, job_name, limit))
    else:
        cursor.execute('''
            SELECT * FROM job_run_log
            WHERE start_time >= ?
            ORDER BY start_time DESC LIMIT ?
        ''', (since, limit))

    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_volume_ratio(code: str, avg_days: int = 20) -> dict:
    """종목의 최근 N일 평균 거래량 대비 당일 거래량 배수 계산.
    반환: {code, date, today_volume, avg_volume, ratio, days_used}
    days_used < avg_days 이면 아직 데이터가 avg_days만큼 쌓이지 않았다는 뜻(참고용으로만 사용).
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT date, MAX(CAST(volume AS INTEGER)) AS volume
        FROM stock_market_cap_daily
        WHERE code = ? AND volume IS NOT NULL AND volume != '' AND volume != '0'
        GROUP BY date
        ORDER BY date DESC
        LIMIT ?
    ''', (code, avg_days + 1))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return {'code': code, 'date': None, 'today_volume': 0, 'avg_volume': 0, 'ratio': 0.0, 'days_used': 0}

    today_row = rows[0]
    past_rows = rows[1:]

    today_volume = today_row['volume']
    days_used = len(past_rows)
    avg_volume = (sum(r['volume'] for r in past_rows) / days_used) if days_used > 0 else 0
    ratio = (today_volume / avg_volume) if avg_volume > 0 else 0.0

    return {
        'code': code,
        'date': today_row['date'],
        'today_volume': today_volume,
        'avg_volume': round(avg_volume, 2),
        'ratio': round(ratio, 3),
        'days_used': days_used,
    }


def get_volume_ratio_batch(date: str = None, avg_days: int = 20, fid_input_iscd: str = "combined") -> list:
    """특정 날짜(기본: 최신일) 기준, 해당 날짜에 데이터가 있는 전 종목의 거래량 배수를 일괄 계산.
    반환: [{code, name, date, today_volume, avg_volume, ratio, days_used}, ...] ratio 내림차순 정렬
    """
    if not date:
        date = get_latest_market_cap_date(fid_input_iscd)
    if not date:
        return []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT code, name FROM stock_market_cap_daily WHERE date = ?
    ''', (date,))
    stocks = [(r['code'], r['name']) for r in cursor.fetchall()]
    conn.close()

    results = []
    for code, name in stocks:
        ratio_info = get_volume_ratio(code, avg_days=avg_days)
        if ratio_info['date'] != date:
            continue
        ratio_info['name'] = name
        results.append(ratio_info)

    results.sort(key=lambda x: x['ratio'], reverse=True)
    return results


def get_volume_collection_status() -> dict:
    """실제 거래량(volume != 0)이 저장된 날짜가 며칠치 쌓였는지 확인.
    20일 평균 계산이 얼마나 신뢰할 만한지 가늠하는 용도.
    반환: {days_collected, latest_date, dates}
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT date FROM stock_market_cap_daily
        WHERE volume IS NOT NULL AND volume != '' AND volume != '0'
        ORDER BY date DESC
    ''')
    dates = [r[0] for r in cursor.fetchall()]
    conn.close()
    return {
        'days_collected': len(dates),
        'latest_date': dates[0] if dates else None,
        'dates': dates,
    }


def _score_momentum(ratio: float, change_rate: float) -> int:
    """거래량 배수 + 등락률 조합 → 모멘텀 점수(0~30점).
    거래량 급증(배수↑) + 가격 상승(등락률↑)이 동시에 나타날수록 고득점.
    거래량은 늘었는데 가격이 빠지는 경우(분산/이탈 신호)는 모멘텀이 아니라 리스크패널티에서
    감점으로 전담 처리한다(get_risk_penalty의 "거래량 급증+당일 하락" 항목) — 중복 반영 방지.
    """
    if ratio >= 3 and change_rate > 3:
        return 30
    if ratio >= 3 and change_rate > 0:
        return 25
    if ratio >= 2 and change_rate > 1:
        return 20
    if ratio >= 2 and change_rate > 0:
        return 15
    if ratio >= 1.5 and change_rate > 0:
        return 10
    return 0


def get_momentum_score(code: str, avg_days: int = 20) -> dict:
    """종목 하나의 모멘텀 점수(거래량 배수 + 당일 등락률 조합, 0~30점) 산출.
    반환: {code, date, ratio, change_rate, score, days_used}
    """
    ratio_info = get_volume_ratio(code, avg_days=avg_days)
    if not ratio_info['date']:
        return {**ratio_info, 'change_rate': None, 'score': 0}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT change_rate FROM stock_market_cap_daily
        WHERE code = ? AND date = ? LIMIT 1
    ''', (code, ratio_info['date']))
    row = cursor.fetchone()
    conn.close()

    change_rate = float(row['change_rate']) if row and row['change_rate'] not in (None, '') else 0.0
    score = _score_momentum(ratio_info['ratio'], change_rate)

    return {
        'code': code,
        'date': ratio_info['date'],
        'ratio': ratio_info['ratio'],
        'change_rate': change_rate,
        'score': score,
        'days_used': ratio_info['days_used'],
    }


def get_momentum_score_batch(date: str = None, avg_days: int = 20, fid_input_iscd: str = "combined") -> list:
    """특정 날짜(기본: 최신일) 기준, 전 종목의 모멘텀 점수를 일괄 계산.
    반환: [{code, name, date, ratio, change_rate, score, days_used}, ...] 점수 내림차순 정렬
    """
    if not date:
        date = get_latest_market_cap_date(fid_input_iscd)
    if not date:
        return []

    ratio_rows = get_volume_ratio_batch(date=date, avg_days=avg_days, fid_input_iscd=fid_input_iscd)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT code, change_rate FROM stock_market_cap_daily WHERE date = ?', (date,))
    change_map = {r['code']: r['change_rate'] for r in cursor.fetchall()}
    conn.close()

    results = []
    for r in ratio_rows:
        cr = change_map.get(r['code'])
        change_rate = float(cr) if cr not in (None, '') else 0.0
        score = _score_momentum(r['ratio'], change_rate)
        results.append({**r, 'change_rate': change_rate, 'score': score})

    results.sort(key=lambda x: x['score'], reverse=True)
    return results


def get_rank_stability_score(code: str, trend_days: int = 5, fid_input_iscd: str = "combined") -> dict:
    """시가총액 랭킹 안정성 점수(0~15점) 산출.
    - 최신 랭킹이 상위 100위 이내면 +10 (소형주 리스크 대비 신뢰도)
    - trend_days 영업일 전 대비 랭킹이 상승(숫자가 작아짐)했으면 +5
    반환: {code, date, rank, rank_before, rank_change, score}
    """
    if fid_input_iscd == "combined":
        iscd_list = ("0001", "1001")
    else:
        iscd_list = (fid_input_iscd,)
    placeholders = ",".join("?" * len(iscd_list))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT date, rank FROM stock_market_cap_daily
        WHERE code = ? AND fid_input_iscd IN ({placeholders})
        ORDER BY date DESC
        LIMIT ?
    ''', (code, *iscd_list, trend_days + 1))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return {'code': code, 'date': None, 'rank': None, 'rank_before': None, 'rank_change': None, 'score': 0}

    rank = rows[0]['rank']
    score = 10 if rank <= 100 else 0

    rank_before = None
    rank_change = None
    if len(rows) > 1:
        before_row = rows[min(trend_days, len(rows) - 1)]
        rank_before = before_row['rank']
        rank_change = rank_before - rank  # 양수 = 랭킹 상승(숫자 감소)
        if rank_change > 0:
            score += 5

    return {
        'code': code,
        'date': rows[0]['date'],
        'rank': rank,
        'rank_before': rank_before,
        'rank_change': rank_change,
        'score': score,
    }


def get_rank_stability_score_batch(date: str = None, trend_days: int = 5, fid_input_iscd: str = "combined") -> list:
    """특정 날짜(기본: 최신일) 기준, 전 종목의 랭킹 안정성 점수를 일괄 계산.
    반환: [{code, name, date, rank, rank_before, rank_change, score}, ...] 점수 내림차순, 동점이면 랭킹 오름차순
    """
    if not date:
        date = get_latest_market_cap_date(fid_input_iscd)
    if not date:
        return []

    if fid_input_iscd == "combined":
        iscd_list = ("0001", "1001")
    else:
        iscd_list = (fid_input_iscd,)
    placeholders = ",".join("?" * len(iscd_list))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT DISTINCT code, name FROM stock_market_cap_daily
        WHERE date = ? AND fid_input_iscd IN ({placeholders})
    ''', (date, *iscd_list))
    stocks = [(r['code'], r['name']) for r in cursor.fetchall()]
    conn.close()

    results = []
    for code, name in stocks:
        info = get_rank_stability_score(code, trend_days=trend_days, fid_input_iscd=fid_input_iscd)
        if info['date'] != date:
            continue
        info['name'] = name
        results.append(info)

    results.sort(key=lambda x: (-x['score'], x['rank']))
    return results


def _score_supply_demand(frgn_total: int, orgn_total: int) -> int:
    """외국인/기관 N일 누적 순매수 조합 → 수급 점수(-15~30점).
    둘 다 순매수면 고득점, 하나만 순매수면 중간 점수, 둘 다 순매도(동반 이탈)면 감점.
    """
    if frgn_total > 0 and orgn_total > 0:
        return 30
    if frgn_total > 0 or orgn_total > 0:
        return 15
    if frgn_total < 0 and orgn_total < 0:
        return -15
    return 0


def get_supply_demand_score(code: str, days: int = 3) -> dict:
    """종목 하나의 외국인/기관 N일(기본 3일) 누적 순매수 기반 수급 점수(-15~30점) 산출.
    반환: {code, date_from, date_to, frgn_total, orgn_total, days_used, score}
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT date,
               CAST(frgn_ntby_tr_pbmn AS INTEGER) AS frgn,
               CAST(orgn_ntby_tr_pbmn AS INTEGER) AS orgn
        FROM stock_investor_daily
        WHERE code = ?
        ORDER BY date DESC
        LIMIT ?
    ''', (code, days))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return {'code': code, 'date_from': None, 'date_to': None,
                'frgn_total': 0, 'orgn_total': 0, 'days_used': 0, 'score': 0}

    frgn_total = sum(r['frgn'] for r in rows)
    orgn_total = sum(r['orgn'] for r in rows)

    return {
        'code': code,
        'date_from': rows[-1]['date'],
        'date_to': rows[0]['date'],
        'frgn_total': frgn_total,
        'orgn_total': orgn_total,
        'days_used': len(rows),
        'score': _score_supply_demand(frgn_total, orgn_total),
    }


def get_supply_demand_score_batch(days: int = 3) -> list:
    """stock_investor_daily에 데이터가 있는 전 종목의 수급 점수를 일괄 계산.
    반환: [{code, name, date_from, date_to, frgn_total, orgn_total, days_used, score}, ...] 점수 내림차순
    (참고: 투자자매매동향은 시총 상위 종목만 수집되므로 전체 상장 종목이 아님)
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT code, name FROM stock_investor_daily')
    stocks = [(r['code'], r['name']) for r in cursor.fetchall()]
    conn.close()

    results = []
    for code, name in stocks:
        info = get_supply_demand_score(code, days=days)
        if info['days_used'] == 0:
            continue
        info['name'] = name
        results.append(info)

    results.sort(key=lambda x: x['score'], reverse=True)
    return results


def _get_index_score(sector_code: str, date: str) -> dict:
    """시장 지수(코스피=0001/코스닥=1001) 당일 등락률 + 5영업일 추세로 점수(0~15점) 산출.
    - 당일 등락률 양수 → +10
    - 5영업일 전 종가 대비 오늘 종가가 높으면(상승 추세) → +5
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT date, close, change_rate FROM sector_index_daily
        WHERE sector_code = ? AND date <= ?
        ORDER BY date DESC LIMIT 6
    ''', (sector_code, date))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return {'index_change_rate': None, 'index_trend_up': None, 'score': 0}

    today = rows[0]
    change_rate = float(today['change_rate']) if today['change_rate'] not in (None, '') else 0.0
    score = 10 if change_rate > 0 else 0

    trend_up = None
    if len(rows) > 1:
        oldest = rows[-1]
        try:
            trend_up = float(today['close']) > float(oldest['close'])
        except (TypeError, ValueError):
            trend_up = None
        if trend_up:
            score += 5

    return {'index_change_rate': change_rate, 'index_trend_up': trend_up, 'score': score}


def get_market_environment_score(code: str, date: str = None) -> dict:
    """종목이 속한 시장(코스피/코스닥) 지수의 환경 점수(0~15점) 산출.
    반환: {code, market, date, index_change_rate, index_trend_up, score}
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if date:
        cursor.execute('''
            SELECT date, fid_input_iscd FROM stock_market_cap_daily
            WHERE code = ? AND date = ? AND fid_input_iscd IN ('0001', '1001')
            LIMIT 1
        ''', (code, date))
    else:
        cursor.execute('''
            SELECT date, fid_input_iscd FROM stock_market_cap_daily
            WHERE code = ? AND fid_input_iscd IN ('0001', '1001')
            ORDER BY date DESC LIMIT 1
        ''', (code,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return {'code': code, 'market': None, 'date': None,
                'index_change_rate': None, 'index_trend_up': None, 'score': 0}

    idx = _get_index_score(row['fid_input_iscd'], row['date'])
    return {'code': code, 'market': row['fid_input_iscd'], 'date': row['date'], **idx}


def get_market_environment_score_batch(date: str = None, fid_input_iscd: str = "combined") -> list:
    """특정 날짜(기본: 최신일) 기준, 전 종목의 시장 환경 점수를 일괄 계산.
    같은 시장(코스피/코스닥) 종목은 지수 점수를 공유하므로 시장별로 한 번만 계산 후 매핑.
    반환: [{code, name, market, date, index_change_rate, index_trend_up, score}, ...]
    """
    if not date:
        date = get_latest_market_cap_date(fid_input_iscd)
    if not date:
        return []

    if fid_input_iscd == "combined":
        iscd_list = ("0001", "1001")
    else:
        iscd_list = (fid_input_iscd,)

    market_scores = {iscd: _get_index_score(iscd, date) for iscd in iscd_list}

    placeholders = ",".join("?" * len(iscd_list))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT DISTINCT code, name, fid_input_iscd FROM stock_market_cap_daily
        WHERE date = ? AND fid_input_iscd IN ({placeholders})
    ''', (date, *iscd_list))
    rows = cursor.fetchall()
    conn.close()

    results = []
    for r in rows:
        idx = market_scores.get(r['fid_input_iscd'], {'index_change_rate': None, 'index_trend_up': None, 'score': 0})
        results.append({
            'code': r['code'], 'name': r['name'], 'market': r['fid_input_iscd'], 'date': date,
            **idx,
        })
    return results


def _get_cumulative_return(code: str, days: int = 5, fid_input_iscd: str = "combined") -> dict:
    """최근 N영업일 누적 상승률(%) 계산 (오늘 종가 vs N영업일 전 종가).
    반환: {date, price_now, price_before, cum_return, days_used}
    """
    if fid_input_iscd == "combined":
        iscd_list = ("0001", "1001")
    else:
        iscd_list = (fid_input_iscd,)
    placeholders = ",".join("?" * len(iscd_list))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT date, price FROM stock_market_cap_daily
        WHERE code = ? AND fid_input_iscd IN ({placeholders})
        ORDER BY date DESC LIMIT ?
    ''', (code, *iscd_list, days + 1))
    rows = cursor.fetchall()
    conn.close()

    if len(rows) < 2:
        return {'date': rows[0]['date'] if rows else None, 'price_now': None,
                'price_before': None, 'cum_return': 0.0, 'days_used': len(rows)}

    price_now = float(rows[0]['price'])
    price_before = float(rows[min(days, len(rows) - 1)]['price'])
    cum_return = ((price_now - price_before) / price_before * 100) if price_before else 0.0

    return {
        'date': rows[0]['date'],
        'price_now': price_now,
        'price_before': price_before,
        'cum_return': round(cum_return, 2),
        'days_used': len(rows) - 1,
    }


def get_risk_penalty(code: str, fid_input_iscd: str = "combined") -> dict:
    """과열·이탈·수급 신호를 감지해 리스크 조정 점수(-25~15점) 산출.
    반영 조건 (근거 데이터가 있는 것만 — 고가/저가/시가가 없어 윗꼬리·갭상승은 제외):
    - 최근 7영업일 누적 상승률 30% 이상(추세 지속 강세로 판단) → +15
    - 당일 거래량 배수 2배 이상인데 등락률이 음수(거래량은 터졌는데 하락, 이탈 신호) → -10
    - 외국인·기관 3일 동반 순매도 → -15
    (합산 후 -20점 하한, 상한은 두지 않음)
    반환: {code, date, cum_return_7d, ratio, change_rate, supply_demand_score, penalties, score}
    """
    momentum = get_momentum_score(code)
    ret_info = _get_cumulative_return(code, days=7, fid_input_iscd=fid_input_iscd)
    supply_info = get_supply_demand_score(code, days=3)

    penalties = []
    if ret_info['cum_return'] >= 30:
        penalties.append({'reason': '최근 7일 강한 상승 추세(+30% 이상)', 'value': 15})
    if momentum['ratio'] >= 2 and momentum.get('change_rate') is not None and momentum['change_rate'] < 0:
        penalties.append({'reason': '거래량 급증+당일 하락(이탈 신호)', 'value': -10})
    if supply_info['score'] == -15:
        penalties.append({'reason': '외국인·기관 동반 순매도', 'value': -15})

    score = max(sum(p['value'] for p in penalties), -20)

    return {
        'code': code,
        'date': momentum.get('date') or ret_info.get('date'),
        'cum_return_7d': ret_info['cum_return'],
        'ratio': momentum['ratio'],
        'change_rate': momentum.get('change_rate'),
        'supply_demand_score': supply_info['score'],
        'penalties': penalties,
        'score': score,
    }


def get_risk_penalty_batch(date: str = None, fid_input_iscd: str = "combined") -> list:
    """특정 날짜(기본: 최신일) 기준, 전 종목의 리스크 패널티를 일괄 계산.
    반환: [{code, name, ...get_risk_penalty 결과...}, ...] 점수 오름차순(가장 위험한 종목이 먼저)
    """
    if not date:
        date = get_latest_market_cap_date(fid_input_iscd)
    if not date:
        return []

    if fid_input_iscd == "combined":
        iscd_list = ("0001", "1001")
    else:
        iscd_list = (fid_input_iscd,)
    placeholders = ",".join("?" * len(iscd_list))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT DISTINCT code, name FROM stock_market_cap_daily
        WHERE date = ? AND fid_input_iscd IN ({placeholders})
    ''', (date, *iscd_list))
    stocks = [(r['code'], r['name']) for r in cursor.fetchall()]
    conn.close()

    results = []
    for code, name in stocks:
        info = get_risk_penalty(code, fid_input_iscd=fid_input_iscd)
        if info['date'] != date:
            continue
        info['name'] = name
        results.append(info)

    results.sort(key=lambda x: x['score'])
    return results


def get_hts_top_view_bonus(code: str, date: str = None) -> dict:
    """HTS조회상위20종목 당일 등장 여부에 따른 가점(0~20점) 산출.
    date 기준(미지정 시 최신 시가총액 날짜)으로 그날 HTS조회상위에 등장했다면 최고 순위 구간별 가점.
    - 1~3위 등장 → +20
    - 4~10위 등장 → +12
    - 11위 이하 등장 → +6
    - 그날 미등장 → 0
    같은 날 여러 시간대에 걸쳐 등장했으면 그중 최고 순위(가장 작은 rank) 기준.
    반환: {code, date, best_rank, appearances, score}
    """
    if not date:
        date = get_latest_market_cap_date()
    if not date:
        return {'code': code, 'date': None, 'best_rank': None, 'appearances': 0, 'score': 0}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT MIN(rank) AS best_rank, COUNT(*) AS appearances
        FROM stock_hts_top_view_hourly
        WHERE code = ? AND date = ?
    ''', (code, date))
    row = cursor.fetchone()
    conn.close()

    best_rank = row['best_rank'] if row and row['best_rank'] is not None else None
    appearances = row['appearances'] if row else 0

    if best_rank is None:
        score = 0
    elif best_rank <= 3:
        score = 20
    elif best_rank <= 10:
        score = 12
    else:
        score = 6

    return {'code': code, 'date': date, 'best_rank': best_rank, 'appearances': appearances, 'score': score}


def get_top_interest_bonus(code: str, date: str = None) -> dict:
    """관심종목등록 상위 당일 등장 여부에 따른 가점(0~10점) 산출.
    HTS조회상위 가점(0~20점)과 합쳐 "관심도 보너스" 최대 30점을 구성하는 두 번째 축.
    date 기준(미지정 시 최신 시가총액 날짜)으로 그날 관심종목등록 상위에 등장했다면 순위 구간별 가점.
    - 1~3위 등장 → +10
    - 4~10위 등장 → +6
    - 11위 이하 등장 → +3
    - 그날 미등장 → 0
    반환: {code, date, rank, reg_count, score}
    """
    if not date:
        date = get_latest_market_cap_date()
    if not date:
        return {'code': code, 'date': None, 'rank': None, 'reg_count': None, 'score': 0}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT rank, reg_count FROM stock_top_interest_daily
        WHERE code = ? AND date = ?
        LIMIT 1
    ''', (code, date))
    row = cursor.fetchone()
    conn.close()

    rank = row['rank'] if row else None
    reg_count = row['reg_count'] if row else None

    if rank is None:
        score = 0
    elif rank <= 3:
        score = 10
    elif rank <= 10:
        score = 6
    else:
        score = 3

    return {'code': code, 'date': date, 'rank': rank, 'reg_count': reg_count, 'score': score}


def _grade_for_score(total: int) -> str:
    """종합 점수 → 알림 등급(A/B/C/제외)."""
    if total >= 80:
        return 'A'
    if total >= 65:
        return 'B'
    if total >= 50:
        return 'C'
    return '제외'


def get_signal_score(code: str, fid_input_iscd: str = "combined") -> dict:
    """1~7번 점수(모멘텀·수급·랭킹안정성·시장환경·리스크패널티·HTS조회상위가점·관심종목등록가점)를
    합산한 종합 Signal Score 산출.
    HTS조회상위가점(0~20)+관심종목등록가점(0~10) = "관심도 보너스" 최대 30점(합산 후 30점 상한).
    등급: A(80점↑) / B(65~79) / C(50~64) / 제외(50 미만)
    반환: {code, date, momentum_score, supply_demand_score, rank_stability_score,
           market_environment_score, risk_penalty_score, hts_top_view_bonus_score,
           top_interest_bonus_score, total, grade, detail}
    """
    momentum = get_momentum_score(code)
    supply = get_supply_demand_score(code, days=3)
    rank = get_rank_stability_score(code, fid_input_iscd=fid_input_iscd)
    market = get_market_environment_score(code)
    risk = get_risk_penalty(code, fid_input_iscd=fid_input_iscd)
    date = momentum.get('date') or rank.get('date') or market.get('date') or risk.get('date')
    hts_bonus = get_hts_top_view_bonus(code, date=date)
    top_interest_bonus = get_top_interest_bonus(code, date=date)
    interest_bonus_total = min(hts_bonus['score'] + top_interest_bonus['score'], 30)

    total = momentum['score'] + supply['score'] + rank['score'] + market['score'] + risk['score'] + interest_bonus_total

    return {
        'code': code,
        'date': date,
        'momentum_score': momentum['score'],
        'supply_demand_score': supply['score'],
        'rank_stability_score': rank['score'],
        'market_environment_score': market['score'],
        'risk_penalty_score': risk['score'],
        'hts_top_view_bonus_score': hts_bonus['score'],
        'top_interest_bonus_score': top_interest_bonus['score'],
        'total': total,
        'grade': _grade_for_score(total),
        'detail': {
            'momentum': momentum,
            'supply_demand': supply,
            'rank_stability': rank,
            'market_environment': market,
            'risk_penalty': risk,
            'hts_top_view_bonus': hts_bonus,
            'top_interest_bonus': top_interest_bonus,
        },
    }


def get_signal_score_batch(date: str = None, fid_input_iscd: str = "combined", save: bool = False) -> list:
    """특정 날짜(기본: 최신일) 기준, 전 종목의 종합 Signal Score를 일괄 계산.
    save=True면 signal_score_daily 테이블에 upsert 저장.
    반환: [{code, name, ...get_signal_score 결과(detail 제외)...}, ...] 총점 내림차순
    """
    if not date:
        date = get_latest_market_cap_date(fid_input_iscd)
    if not date:
        return []

    if fid_input_iscd == "combined":
        iscd_list = ("0001", "1001")
    else:
        iscd_list = (fid_input_iscd,)
    placeholders = ",".join("?" * len(iscd_list))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT DISTINCT code, name FROM stock_market_cap_daily
        WHERE date = ? AND fid_input_iscd IN ({placeholders})
    ''', (date, *iscd_list))
    stocks = [(r['code'], r['name']) for r in cursor.fetchall()]
    conn.close()

    results = []
    for code, name in stocks:
        info = get_signal_score(code, fid_input_iscd=fid_input_iscd)
        if info['date'] != date:
            continue
        info['name'] = name
        results.append(info)

    results.sort(key=lambda x: x['total'], reverse=True)

    if save:
        save_signal_score_daily(results)

    return results


def save_signal_score_daily(rows: list) -> int:
    """get_signal_score_batch() 결과를 signal_score_daily 테이블에 upsert 저장."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    saved = 0
    for r in rows:
        cursor.execute('''
            INSERT INTO signal_score_daily
                (date, code, name, momentum_score, supply_demand_score, rank_stability_score,
                 market_environment_score, risk_penalty_score, hts_top_view_bonus_score,
                 top_interest_bonus_score, total_score, grade, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, code) DO UPDATE SET
                name=excluded.name, momentum_score=excluded.momentum_score,
                supply_demand_score=excluded.supply_demand_score,
                rank_stability_score=excluded.rank_stability_score,
                market_environment_score=excluded.market_environment_score,
                risk_penalty_score=excluded.risk_penalty_score,
                hts_top_view_bonus_score=excluded.hts_top_view_bonus_score,
                top_interest_bonus_score=excluded.top_interest_bonus_score,
                total_score=excluded.total_score, grade=excluded.grade, timestamp=excluded.timestamp
        ''', (r['date'], r['code'], r['name'], r['momentum_score'], r['supply_demand_score'],
              r['rank_stability_score'], r['market_environment_score'], r['risk_penalty_score'],
              r.get('hts_top_view_bonus_score', 0), r.get('top_interest_bonus_score', 0),
              r['total'], r['grade'], timestamp))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def get_signal_score_history(date: str = None, grade: str = None, limit: int = 100) -> list:
    """signal_score_daily 저장된 결과 조회. date 미지정 시 최신 저장 날짜 기준."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if not date:
        cursor.execute('SELECT MAX(date) FROM signal_score_daily')
        row = cursor.fetchone()
        date = row[0] if row and row[0] else None
    if not date:
        conn.close()
        return []

    if grade:
        cursor.execute('''
            SELECT * FROM signal_score_daily WHERE date = ? AND grade = ?
            ORDER BY total_score DESC LIMIT ?
        ''', (date, grade, limit))
    else:
        cursor.execute('''
            SELECT * FROM signal_score_daily WHERE date = ?
            ORDER BY total_score DESC LIMIT ?
        ''', (date, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_signal_score_range(date_from: str, date_to: str) -> list:
    """signal_score_daily 저장된 결과를 구간(date_from~date_to)으로 조회 — 날짜별 추이 매트릭스용.
    반환: [{date, code, name, ...점수 필드들..., total_score, grade}, ...] (날짜 오름차순, 날짜 내 총점 내림차순)
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM signal_score_daily
        WHERE date BETWEEN ? AND ?
        ORDER BY date ASC, total_score DESC
    ''', (date_from, date_to))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# Initialize DB on load
init_db()


# ──────────────────────────────────────────────
# 데이터 동기화 (로컬 → 서버 push) 함수들
# ──────────────────────────────────────────────

def sync_upsert_market_cap(rows: list) -> int:
    """stock_market_cap_daily upsert (date+code+fid_input_iscd 기준)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    saved = 0
    for r in rows:
        cursor.execute('''
            INSERT INTO stock_market_cap_daily
                (date, code, name, market_cap_amount, rank, price, change_rate, market_weight, fid_input_iscd, volume, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, code, fid_input_iscd) DO UPDATE SET
                name=excluded.name, market_cap_amount=excluded.market_cap_amount,
                rank=excluded.rank, price=excluded.price, change_rate=excluded.change_rate,
                market_weight=excluded.market_weight, volume=excluded.volume, timestamp=excluded.timestamp
        ''', (r.get('date'), r.get('code'), r.get('name'), r.get('market_cap_amount'),
              r.get('rank'), r.get('price'), r.get('change_rate'),
              r.get('market_weight', '0'), r.get('fid_input_iscd', '0001'), r.get('volume', '0'),
              r.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def sync_upsert_investor_daily(rows: list) -> int:
    """stock_investor_daily upsert (date+code 기준)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    saved = 0
    for r in rows:
        cursor.execute('''
            INSERT INTO stock_investor_daily
                (date, code, name, frgn_ntby_qty, frgn_ntby_tr_pbmn,
                 prsn_ntby_qty, prsn_ntby_tr_pbmn, orgn_ntby_qty, orgn_ntby_tr_pbmn, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, code) DO UPDATE SET
                name=excluded.name, frgn_ntby_qty=excluded.frgn_ntby_qty,
                frgn_ntby_tr_pbmn=excluded.frgn_ntby_tr_pbmn,
                prsn_ntby_qty=excluded.prsn_ntby_qty, prsn_ntby_tr_pbmn=excluded.prsn_ntby_tr_pbmn,
                orgn_ntby_qty=excluded.orgn_ntby_qty, orgn_ntby_tr_pbmn=excluded.orgn_ntby_tr_pbmn,
                timestamp=excluded.timestamp
        ''', (r.get('date'), r.get('code'), r.get('name'),
              r.get('frgn_ntby_qty', '0'), r.get('frgn_ntby_tr_pbmn', '0'),
              r.get('prsn_ntby_qty', '0'), r.get('prsn_ntby_tr_pbmn', '0'),
              r.get('orgn_ntby_qty', '0'), r.get('orgn_ntby_tr_pbmn', '0'),
              r.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def sync_upsert_investor_trend(rows: list) -> int:
    """investor_trend_daily upsert (date+exch_div+mrkt_div+invr_cls_code 기준)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    saved = 0
    for r in rows:
        cursor.execute('''
            INSERT INTO investor_trend_daily
                (date, exch_div, mrkt_div, invr_cls_code, invr_cls_name,
                 all_shnu_amt, all_seln_amt, all_ntby_amt, all_shnu_qty, all_seln_qty, all_ntby_qty,
                 arbt_shnu_amt, arbt_seln_amt, arbt_ntby_amt, arbt_shnu_qty, arbt_seln_qty, arbt_ntby_qty,
                 nabt_shnu_amt, nabt_seln_amt, nabt_ntby_amt, nabt_shnu_qty, nabt_seln_qty, nabt_ntby_qty,
                 timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, exch_div, mrkt_div, invr_cls_code) DO UPDATE SET
                invr_cls_name=excluded.invr_cls_name,
                all_shnu_amt=excluded.all_shnu_amt, all_seln_amt=excluded.all_seln_amt, all_ntby_amt=excluded.all_ntby_amt,
                all_shnu_qty=excluded.all_shnu_qty, all_seln_qty=excluded.all_seln_qty, all_ntby_qty=excluded.all_ntby_qty,
                timestamp=excluded.timestamp
        ''', (r.get('date'), r.get('exch_div'), r.get('mrkt_div'), r.get('invr_cls_code'), r.get('invr_cls_name'),
              r.get('all_shnu_amt','0'), r.get('all_seln_amt','0'), r.get('all_ntby_amt','0'),
              r.get('all_shnu_qty','0'), r.get('all_seln_qty','0'), r.get('all_ntby_qty','0'),
              r.get('arbt_shnu_amt','0'), r.get('arbt_seln_amt','0'), r.get('arbt_ntby_amt','0'),
              r.get('arbt_shnu_qty','0'), r.get('arbt_seln_qty','0'), r.get('arbt_ntby_qty','0'),
              r.get('nabt_shnu_amt','0'), r.get('nabt_seln_amt','0'), r.get('nabt_ntby_amt','0'),
              r.get('nabt_shnu_qty','0'), r.get('nabt_seln_qty','0'), r.get('nabt_ntby_qty','0'),
              r.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def sync_upsert_sector_index(rows: list) -> int:
    """sector_index_daily upsert (date+sector_code 기준)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    saved = 0
    for r in rows:
        cursor.execute('''
            INSERT INTO sector_index_daily
                (date, sector_code, sector_name, close, open, high, low,
                 change, change_sign, change_rate, volume, trade_amount, vol_ratio, psychology_index, d20_dsrt, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, sector_code) DO UPDATE SET
                sector_name=excluded.sector_name, close=excluded.close, open=excluded.open,
                high=excluded.high, low=excluded.low, change=excluded.change,
                change_sign=excluded.change_sign, change_rate=excluded.change_rate,
                volume=excluded.volume, trade_amount=excluded.trade_amount,
                vol_ratio=excluded.vol_ratio, psychology_index=excluded.psychology_index,
                d20_dsrt=excluded.d20_dsrt, timestamp=excluded.timestamp
        ''', (r.get('date'), r.get('sector_code'), r.get('sector_name'),
              r.get('close','0'), r.get('open','0'), r.get('high','0'), r.get('low','0'),
              r.get('change','0'), r.get('change_sign','3'), r.get('change_rate','0'),
              r.get('volume','0'), r.get('trade_amount','0'), r.get('vol_ratio','0'),
              r.get('psychology_index', r.get('net_buy', '0')), r.get('d20_dsrt','0'),
              r.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def get_sector_index_cached(sector_code: str, limit: int = 30) -> list:
    """sector_index_daily 캐시 조회"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM sector_index_daily
        WHERE sector_code = ?
        ORDER BY date DESC LIMIT ?
    ''', (sector_code, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_investor_trend_cached(exch_div: str = 'J', mrkt_div: str = '1') -> list:
    """investor_trend_daily 최신 날짜 데이터 조회"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM investor_trend_daily
        WHERE exch_div = ? AND mrkt_div = ?
        ORDER BY date DESC, invr_cls_code ASC
        LIMIT 20
    ''', (exch_div, mrkt_div))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_sector_index_daily(records: list, iscd: str, sector_name: str):
    """업종 일자별지수 저장 (KIS API output2 원시 레코드 → sector_index_daily upsert)"""
    SIGN = {'1': '상한', '2': '상승', '3': '보합', '4': '하한', '5': '하락'}
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    saved = 0
    for r in records:
        d = r.get('stck_bsop_date', '')
        if len(d) != 8:
            continue
        date = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        cursor.execute('''
            INSERT INTO sector_index_daily
                (date, sector_code, sector_name, close, open, high, low,
                 change, change_sign, change_rate, volume, trade_amount, vol_ratio, psychology_index, d20_dsrt, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, sector_code) DO UPDATE SET
                sector_name=excluded.sector_name, close=excluded.close, open=excluded.open,
                high=excluded.high, low=excluded.low, change=excluded.change,
                change_sign=excluded.change_sign, change_rate=excluded.change_rate,
                volume=excluded.volume, trade_amount=excluded.trade_amount,
                vol_ratio=excluded.vol_ratio, psychology_index=excluded.psychology_index,
                d20_dsrt=excluded.d20_dsrt, timestamp=excluded.timestamp
        ''', (
            date, iscd, sector_name,
            r.get('bstp_nmix_prpr', '0'), r.get('bstp_nmix_oprc', '0'),
            r.get('bstp_nmix_hgpr', '0'), r.get('bstp_nmix_lwpr', '0'),
            r.get('bstp_nmix_prdy_vrss', '0'),
            SIGN.get(r.get('prdy_vrss_sign', '3'), '보합'),
            r.get('bstp_nmix_prdy_ctrt', '0'),
            r.get('acml_vol', '0'), r.get('acml_tr_pbmn', '0'),
            r.get('acml_vol_rlim', '0'), r.get('invt_new_psdg', '0'),
            r.get('d20_dsrt', '0'), timestamp
        ))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def save_sector_stocks_daily(records: list, iscd: str, sector_name: str):
    """업종 소속 종목 저장 (KIS 등락률 순위 API 원시 레코드 → sector_stocks_daily upsert, 당일 스냅샷)"""
    SIGN = {'1': '상한', '2': '상승', '3': '보합', '4': '하한', '5': '하락'}
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    date = datetime.now().strftime('%Y-%m-%d')
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    saved = 0
    for r in records:
        code = r.get('stck_shrn_iscd', '')
        if not code:
            continue
        cursor.execute('''
            INSERT INTO sector_stocks_daily
                (date, sector_code, sector_name, rank, code, name, price, change, change_sign, change_rate, volume, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, sector_code, code) DO UPDATE SET
                sector_name=excluded.sector_name, rank=excluded.rank, name=excluded.name,
                price=excluded.price, change=excluded.change, change_sign=excluded.change_sign,
                change_rate=excluded.change_rate, volume=excluded.volume, timestamp=excluded.timestamp
        ''', (
            date, iscd, sector_name,
            r.get('data_rank', '0'), code, r.get('hts_kor_isnm', ''),
            r.get('stck_prpr', '0'), r.get('prdy_vrss', '0'),
            SIGN.get(r.get('prdy_vrss_sign', '3'), '보합'),
            r.get('prdy_ctrt', '0'), r.get('acml_vol', '0'), timestamp
        ))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def get_sector_stocks_cached(sector_code: str, limit: int = 30) -> list:
    """sector_stocks_daily 최신 날짜 캐시 조회"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM sector_stocks_daily
        WHERE sector_code = ? AND date = (
            SELECT MAX(date) FROM sector_stocks_daily WHERE sector_code = ?
        )
        ORDER BY CAST(rank AS INTEGER) ASC LIMIT ?
    ''', (sector_code, sector_code, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def sync_upsert_sector_stocks(rows: list) -> int:
    """sector_stocks_daily upsert (date+sector_code+code 기준) — 원격 동기화 수신용"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    saved = 0
    for r in rows:
        cursor.execute('''
            INSERT INTO sector_stocks_daily
                (date, sector_code, sector_name, rank, code, name, price, change, change_sign, change_rate, volume, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, sector_code, code) DO UPDATE SET
                sector_name=excluded.sector_name, rank=excluded.rank, name=excluded.name,
                price=excluded.price, change=excluded.change, change_sign=excluded.change_sign,
                change_rate=excluded.change_rate, volume=excluded.volume, timestamp=excluded.timestamp
        ''', (r.get('date'), r.get('sector_code'), r.get('sector_name'),
              r.get('rank', '0'), r.get('code'), r.get('name', ''),
              r.get('price', '0'), r.get('change', '0'), r.get('change_sign', '3'),
              r.get('change_rate', '0'), r.get('volume', '0'),
              r.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def get_stock_memos(code: str, limit: int = 100) -> list:
    """종목의 전체 메모 이력 조회 (최신순). 종목당 여러 개 저장 가능."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM stock_memo WHERE code = ? ORDER BY created_at DESC, id DESC LIMIT ?
    ''', (code, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_stock_memo(code: str, name: str, memo: str) -> int:
    """종목 메모 새로 추가 (항상 새 로그 항목으로 INSERT). 반환: 생성된 row id."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO stock_memo (code, name, memo, created_at) VALUES (?, ?, ?, ?)
    ''', (code, name, memo, timestamp))
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return new_id


def delete_stock_memo_entry(memo_id: int) -> None:
    """메모 항목 1건 삭제 (id 기준)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM stock_memo WHERE id = ?', (memo_id,))
    conn.commit()
    conn.close()


def search_stock_memos(query: str = None, limit: int = 50) -> list:
    """종목별 가장 최근 메모 1건씩 검색 (종목코드/종목명 부분일치) — query 없으면 전체 종목 최신순.
    "다른 종목 메모 검색" 목록용 — 종목당 여러 메모가 있어도 최신 1건만 대표로 보여줌.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    like = f'%{query}%' if query else '%'
    cursor.execute('''
        SELECT s1.* FROM stock_memo s1
        INNER JOIN (
            SELECT code, MAX(id) AS max_id FROM stock_memo GROUP BY code
        ) s2 ON s1.code = s2.code AND s1.id = s2.max_id
        WHERE s1.code LIKE ? OR s1.name LIKE ?
        ORDER BY s1.created_at DESC LIMIT ?
    ''', (like, like, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_investor_dates(limit: int = 10) -> list:
    """stock_investor_daily에 존재하는 최근 N영업일 날짜 목록(내림차순).
    주말/공휴일은 애초에 데이터가 없으므로 달력일이 아닌 실제 거래일 기준으로 안전하게 계산됨.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT date FROM stock_investor_daily ORDER BY date DESC LIMIT ?', (limit,))
    dates = [r[0] for r in cursor.fetchall()]
    conn.close()
    return dates


def get_investor_cross_distribution(date_from: str, date_to: str, top_n: int = 60) -> list:
    """외국인+기관 합산금액을 종목별로 반환 (십자 분포도용).
    반환: [{code, name, frgn_total(signed), orgn_total(signed), frgn_days, orgn_days}]
    |frgn_total| + |orgn_total| 기준 상위 top_n개
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT code, name,
               SUM(CAST(frgn_ntby_tr_pbmn AS INTEGER)) AS frgn_total,
               SUM(CAST(orgn_ntby_tr_pbmn AS INTEGER)) AS orgn_total,
               COUNT(CASE WHEN CAST(frgn_ntby_tr_pbmn AS INTEGER) != 0 THEN 1 END) AS frgn_days,
               COUNT(CASE WHEN CAST(orgn_ntby_tr_pbmn AS INTEGER) != 0 THEN 1 END) AS orgn_days
        FROM stock_investor_daily
        WHERE date BETWEEN ? AND ?
        GROUP BY code, name
        HAVING frgn_days > 0 OR orgn_days > 0
        ORDER BY (ABS(SUM(CAST(frgn_ntby_tr_pbmn AS INTEGER))) +
                  ABS(SUM(CAST(orgn_ntby_tr_pbmn AS INTEGER)))) DESC
        LIMIT ?
    ''', (date_from, date_to, top_n))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_investor_distribution(date_from: str, date_to: str, investor: str, top_n: int = 40) -> list:
    """날짜 범위 내 종목별 순매수/순매도 분리 집계 (분포도용).
    investor: 'frgn' | 'orgn'
    반환: [{code, name,
            buy_amount(양수합산), buy_days,
            sell_amount(음수합산 절대값), sell_days}, ...]
          |buy_amount| + |sell_amount| 기준 상위 top_n개
    """
    col_amount = 'frgn_ntby_tr_pbmn' if investor == 'frgn' else 'orgn_ntby_tr_pbmn'

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT code, name,
               COALESCE(SUM(CASE WHEN CAST({col_amount} AS INTEGER) > 0
                            THEN CAST({col_amount} AS INTEGER) END), 0)   AS buy_amount,
               COUNT(CASE WHEN CAST({col_amount} AS INTEGER) > 0 THEN 1 END) AS buy_days,
               COALESCE(ABS(SUM(CASE WHEN CAST({col_amount} AS INTEGER) < 0
                                THEN CAST({col_amount} AS INTEGER) END)), 0) AS sell_amount,
               COUNT(CASE WHEN CAST({col_amount} AS INTEGER) < 0 THEN 1 END) AS sell_days
        FROM stock_investor_daily
        WHERE date BETWEEN ? AND ?
          AND CAST({col_amount} AS INTEGER) != 0
        GROUP BY code, name
        HAVING buy_days > 0 OR sell_days > 0
        ORDER BY (COALESCE(SUM(CASE WHEN CAST({col_amount} AS INTEGER) > 0
                               THEN CAST({col_amount} AS INTEGER) END), 0) +
                  COALESCE(ABS(SUM(CASE WHEN CAST({col_amount} AS INTEGER) < 0
                                   THEN CAST({col_amount} AS INTEGER) END)), 0)) DESC
        LIMIT ?
    ''', (date_from, date_to, top_n))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_investor_ranking(date_from: str, date_to: str, investor: str, direction: str, top_n: int = 20) -> dict:
    """날짜 범위별 투자자 순매수/순매도 랭킹.
    investor: 'frgn' | 'orgn'
    direction: 'buy' (순매수, 금액 > 0) | 'sell' (순매도, 금액 < 0)
    반환: {date: [{code, name, qty, amount}, ...], dates: [...]}
    """
    col_qty    = 'frgn_ntby_qty'    if investor == 'frgn' else 'orgn_ntby_qty'
    col_amount = 'frgn_ntby_tr_pbmn' if investor == 'frgn' else 'orgn_ntby_tr_pbmn'
    sign = '>' if direction == 'buy' else '<'
    order = 'DESC' if direction == 'buy' else 'ASC'

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(f'''
        SELECT DISTINCT date FROM stock_investor_daily
        WHERE date BETWEEN ? AND ?
        ORDER BY date DESC
    ''', (date_from, date_to))
    dates = [r['date'] for r in cursor.fetchall()]

    result = {}
    for d in dates:
        cursor.execute(f'''
            SELECT code, name,
                   CAST({col_qty} AS INTEGER)    AS qty,
                   CAST({col_amount} AS INTEGER)  AS amount
            FROM stock_investor_daily
            WHERE date = ? AND CAST({col_amount} AS INTEGER) {sign} 0
            ORDER BY CAST({col_amount} AS INTEGER) {order}
            LIMIT ?
        ''', (d, top_n))
        result[d] = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return {'dates': dates, 'data': result}


def get_stock_investor_raw(limit_dates: int = 30) -> list:
    """stock_investor_daily 원시 데이터 조회 (sync export용)"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM stock_investor_daily
        WHERE date IN (
            SELECT DISTINCT date FROM stock_investor_daily
            ORDER BY date DESC LIMIT ?
        )
        ORDER BY date DESC, code ASC
    ''', (limit_dates,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]
