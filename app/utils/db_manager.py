import sqlite3
from datetime import datetime
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
            net_buy TEXT, d20_dsrt TEXT,
            timestamp TEXT,
            UNIQUE(date, sector_code)
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
    거래량은 늘었는데 가격이 빠지면(분산/이탈 신호) 낮은 점수로 처리.
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
    if ratio >= 2 and change_rate < 0:
        return 5
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
                 change, change_sign, change_rate, volume, trade_amount, vol_ratio, net_buy, d20_dsrt, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, sector_code) DO UPDATE SET
                sector_name=excluded.sector_name, close=excluded.close, open=excluded.open,
                high=excluded.high, low=excluded.low, change=excluded.change,
                change_sign=excluded.change_sign, change_rate=excluded.change_rate,
                volume=excluded.volume, trade_amount=excluded.trade_amount,
                vol_ratio=excluded.vol_ratio, net_buy=excluded.net_buy,
                d20_dsrt=excluded.d20_dsrt, timestamp=excluded.timestamp
        ''', (r.get('date'), r.get('sector_code'), r.get('sector_name'),
              r.get('close','0'), r.get('open','0'), r.get('high','0'), r.get('low','0'),
              r.get('change','0'), r.get('change_sign','3'), r.get('change_rate','0'),
              r.get('volume','0'), r.get('trade_amount','0'), r.get('vol_ratio','0'),
              r.get('net_buy','0'), r.get('d20_dsrt','0'),
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
                 change, change_sign, change_rate, volume, trade_amount, vol_ratio, net_buy, d20_dsrt, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date, sector_code) DO UPDATE SET
                sector_name=excluded.sector_name, close=excluded.close, open=excluded.open,
                high=excluded.high, low=excluded.low, change=excluded.change,
                change_sign=excluded.change_sign, change_rate=excluded.change_rate,
                volume=excluded.volume, trade_amount=excluded.trade_amount,
                vol_ratio=excluded.vol_ratio, net_buy=excluded.net_buy,
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
        ORDER BY date ASC
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
