"""과거 캔들 로컬 캐시(SQLite) — 수집과 시뮬레이션을 갈라놓는 중간 저장소.

업비트 캔들 API는 한 번에 200개씩만 주기 때문에, 전 종목 3개월치를 모으려면 수천 번 호출해야 한다
(KRW 280종목 × 1시간봉 90일 ≈ 3천 회, 8req/s로 6~7분). 백테스트는 파라미터를 바꿔가며 여러 번
돌리는 게 본질인데 그때마다 다시 받아올 수는 없으므로, 받은 캔들은 여기에 쌓아 두고 엔진은 이
캐시만 읽는다. 덕분에 엔진 쪽은 네트워크 의존이 아예 없다.

alerts.db와 파일을 분리한 이유: 1시간봉 3개월이면 60만 행이라 운영 DB에 섞으면 백업/조회가 같이
무거워진다. 캔들은 언제든 다시 받을 수 있는 파생 데이터라 날려도 되는 파일로 둔다.

ts는 캔들이 "열린" 시각의 UTC epoch 초(정수)다. 업비트 응답의 candle_date_time_utc를 그대로 옮긴 값
이라 종목/봉 종류가 달라도 같은 기준으로 비교된다. 화면·순위 판정에 필요한 KST 날짜는 읽는 쪽에서
KST로 변환해 쓴다(업비트의 "당일"은 KST 00시 기준이라 UTC로 자르면 순위가 어긋난다).
"""
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Dict, Iterable, List, Optional

from app.config import Config

KST = timezone(timedelta(hours=9))

DB_PATH = getattr(Config, 'BACKTEST_DB_NAME', 'backtest_candles.db')

# 업비트 캔들 종류 → REST 경로. 백테스트의 시간 해상도이자 RSI 계산 봉이기도 하다.
INTERVAL_URLS = {
    'days': '/v1/candles/days',
    'minutes240': '/v1/candles/minutes/240',
    'minutes60': '/v1/candles/minutes/60',
    'minutes15': '/v1/candles/minutes/15',
    'minutes5': '/v1/candles/minutes/5',
}

# 봉 하나의 길이(초) — 진행 구간을 훑을 때와 "다음 봉"을 찾을 때 쓴다
INTERVAL_SECONDS = {
    'days': 86400,
    'minutes240': 4 * 3600,
    'minutes60': 3600,
    'minutes15': 15 * 60,
    'minutes5': 5 * 60,
}


def _connect(db_path: str = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str = None) -> None:
    """캐시 테이블 생성(이미 있으면 그대로). 수집기/엔진 양쪽에서 부담 없이 부를 수 있게 멱등하다."""
    conn = _connect(db_path)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS candles (
            market TEXT NOT NULL,
            interval TEXT NOT NULL,
            ts INTEGER NOT NULL,          -- 캔들이 열린 시각(UTC epoch 초)
            open REAL, high REAL, low REAL, close REAL,
            volume REAL,                  -- candle_acc_trade_volume (이 봉의 체결량)
            value REAL,                   -- candle_acc_trade_price  (이 봉의 원화 거래대금)
            prev_close REAL,              -- 일봉에만 있는 전일 종가(업비트가 직접 준다)
            PRIMARY KEY (market, interval, ts)
        )
    ''')
    # 수집 이력 — 어디까지 받아뒀는지 알아야 이어받기가 되고, 리포트에 "무슨 데이터로 돌린 결과"를 적을 수 있다
    cur.execute('''
        CREATE TABLE IF NOT EXISTS collect_log (
            interval TEXT NOT NULL,
            market TEXT NOT NULL,
            first_ts INTEGER, last_ts INTEGER, row_count INTEGER,
            collected_at TEXT,
            PRIMARY KEY (interval, market)
        )
    ''')
    conn.commit()
    conn.close()


def normalize_candle(raw: dict) -> dict:
    """업비트 캔들 응답 1건 → 캐시에 넣을 형태.

    일봉에는 prev_closing_price가 있고 분봉에는 없다. 분봉 쪽 전일 종가는 같은 종목의 일봉에서
    찾아 쓰므로(market_data.py) 여기서 억지로 채우지 않고 None으로 둔다.
    """
    dt = datetime.strptime(raw['candle_date_time_utc'][:19], '%Y-%m-%dT%H:%M:%S')
    return {
        'ts': int(dt.replace(tzinfo=timezone.utc).timestamp()),
        'open': float(raw['opening_price']),
        'high': float(raw['high_price']),
        'low': float(raw['low_price']),
        'close': float(raw['trade_price']),
        'volume': float(raw.get('candle_acc_trade_volume') or 0),
        'value': float(raw.get('candle_acc_trade_price') or 0),
        'prev_close': float(raw['prev_closing_price']) if raw.get('prev_closing_price') is not None else None,
    }


def upsert_candles(market: str, interval: str, candles: Iterable[dict], db_path: str = None) -> int:
    """정규화된 캔들을 저장. 같은 (종목, 봉, 시각)이 다시 들어오면 덮어쓴다 — 수집을 중간에 끊었다가
    다시 돌려도 중복 없이 이어지게 하려는 것(페이징 경계가 겹치는 건 정상이다)."""
    rows = [
        (market, interval, c['ts'], c['open'], c['high'], c['low'], c['close'],
         c['volume'], c['value'], c['prev_close'])
        for c in candles
    ]
    if not rows:
        return 0
    conn = _connect(db_path)
    conn.executemany(
        'INSERT OR REPLACE INTO candles '
        '(market, interval, ts, open, high, low, close, volume, value, prev_close) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        rows,
    )
    conn.commit()
    conn.close()
    return len(rows)


def record_collect(market: str, interval: str, db_path: str = None) -> None:
    """이 종목/봉을 어디서 어디까지 받아뒀는지 갱신한다."""
    conn = _connect(db_path)
    row = conn.execute(
        'SELECT MIN(ts) AS a, MAX(ts) AS b, COUNT(*) AS n FROM candles WHERE market=? AND interval=?',
        (market, interval),
    ).fetchone()
    conn.execute(
        'INSERT OR REPLACE INTO collect_log (interval, market, first_ts, last_ts, row_count, collected_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (interval, market, row['a'], row['b'], row['n'],
         datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')),
    )
    conn.commit()
    conn.close()


def market_range(market: str, interval: str, db_path: str = None) -> Optional[tuple]:
    """이 종목/봉이 캐시에 있는 구간 (first_ts, last_ts). 하나도 없으면 None."""
    conn = _connect(db_path)
    row = conn.execute(
        'SELECT MIN(ts) AS a, MAX(ts) AS b FROM candles WHERE market=? AND interval=?',
        (market, interval),
    ).fetchone()
    conn.close()
    if row is None or row['a'] is None:
        return None
    return (int(row['a']), int(row['b']))


def list_markets(interval: str, db_path: str = None) -> List[str]:
    conn = _connect(db_path)
    rows = conn.execute(
        'SELECT DISTINCT market FROM candles WHERE interval=? ORDER BY market', (interval,)
    ).fetchall()
    conn.close()
    return [r['market'] for r in rows]


def load_candles(interval: str, start_ts: int = None, end_ts: int = None,
                 markets: List[str] = None, db_path: str = None) -> Dict[str, List[dict]]:
    """종목별 캔들을 시간 오름차순으로 한 번에 읽어 {종목: [캔들...]}로 돌려준다.

    백테스트는 같은 데이터를 수천 번 되짚으므로 매 시각마다 DB를 때리면 느리다 — 시작할 때 한 번에
    올려두고 이후엔 메모리에서만 본다(1시간봉 3개월 ≈ 60만 행, 수백 MB 안쪽).
    """
    sql = 'SELECT market, ts, open, high, low, close, volume, value, prev_close FROM candles WHERE interval=?'
    params: list = [interval]
    if start_ts is not None:
        sql += ' AND ts >= ?'
        params.append(int(start_ts))
    if end_ts is not None:
        sql += ' AND ts <= ?'
        params.append(int(end_ts))
    if markets:
        sql += ' AND market IN (%s)' % ','.join('?' * len(markets))
        params.extend(markets)
    sql += ' ORDER BY market, ts'

    conn = _connect(db_path)
    out: Dict[str, List[dict]] = {}
    for r in conn.execute(sql, params):
        out.setdefault(r['market'], []).append({
            'ts': int(r['ts']), 'open': r['open'], 'high': r['high'], 'low': r['low'],
            'close': r['close'], 'volume': r['volume'], 'value': r['value'],
            'prev_close': r['prev_close'],
        })
    conn.close()
    return out


def coverage(interval: str, db_path: str = None) -> dict:
    """캐시 현황 — "지금 무슨 데이터로 돌릴 수 있는지"를 CLI와 리포트 머리말에 적기 위한 값."""
    conn = _connect(db_path)
    row = conn.execute(
        'SELECT COUNT(DISTINCT market) AS m, COUNT(*) AS n, MIN(ts) AS a, MAX(ts) AS b '
        'FROM candles WHERE interval=?', (interval,)
    ).fetchone()
    conn.close()
    return {
        'interval': interval,
        'market_count': row['m'] or 0,
        'row_count': row['n'] or 0,
        'first_ts': int(row['a']) if row['a'] is not None else None,
        'last_ts': int(row['b']) if row['b'] is not None else None,
    }


def to_kst(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, KST)


def kst_date(ts: int) -> str:
    """이 캔들이 속한 KST 날짜('YYYY-MM-DD') — 업비트의 "당일"은 KST 00시부터라 순위의 기준이 된다."""
    return to_kst(ts).strftime('%Y-%m-%d')


def kst_str(ts: int) -> str:
    return to_kst(ts).strftime('%Y-%m-%d %H:%M:%S')


def db_size_mb(db_path: str = None) -> float:
    path = db_path or DB_PATH
    return os.path.getsize(path) / 1024 / 1024 if os.path.exists(path) else 0.0
