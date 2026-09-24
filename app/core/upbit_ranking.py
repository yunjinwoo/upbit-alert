"""업비트 KRW 마켓 당일 순위 조회 — 상승률 상위 / 거래대금 상위.

기존 코인 스크리닝(app/core/upbit_market_analysis.py)은 종목마다 캔들을 받아 지표를 계산하느라
2~4분이 걸린다. 여기서는 "지금 뭐가 오르고 뭐에 돈이 몰렸나"만 보면 되므로 업비트의 전 종목 현재가
엔드포인트(/v1/ticker/all) 한 번으로 끝낸다 — 호출 1회, 응답 즉시라 화면에서 바로 새로고침해도 된다.

기준(업비트 화면과 동일):
  - 당일 등락률   signed_change_rate — 전일 종가(KST 00시) 대비 현재가
  - 당일 거래대금 acc_trade_price    — KST 00시부터 누적된 원화 거래대금
    (acc_trade_price_24h는 최근 24시간 기준이라 별도 필드로 같이 담아 둔다)

순위 자체는 진입 근거가 아니다. 상승률 상위는 "이미 오른 결과"라 그것만 보고 매매 대상에 넣으면
추격매수가 된다. 그래서 각 종목에 기존 스크리닝 후보(coin_screening_daily의 돌파/구름위/모멘텀)와
겹치는지, 이미 관심 등록(watchlist)돼 있는지를 같이 실어 보낸다 — 진입 근거는 스크리닝 신호가
대고, 순위는 그중 어디에 돈과 관심이 몰렸는지 고르는 필터로만 쓰라는 뜻이다.
"""
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Callable, Optional

import requests

from app.config import Config
from app.utils.db_manager import (
    ENTRY_SIGNALS,
    get_coin_screening_candidates,
    get_watchlist_tickers,
    save_coin_ranking_snapshot,
    has_coin_ranking_snapshot,
    delete_coin_ranking_before,
    get_coin_ranking_history,
)
from app.utils.logger import get_logger

logger = get_logger()

TICKER_ALL_URL = "https://api.upbit.com/v1/ticker/all"
REQUEST_TIMEOUT_SEC = 10
DEFAULT_LIMIT = 10
KST = timezone(timedelta(hours=9))

# 겹침 표시가 바라보는 대상 — 업비트 실거래 대시보드의 "매매 대상"과 같은 집합이어야 의미가 있다.
SCREENING_BROKER = 'upbit'
SCREENING_MODE = 'live'


def fetch_krw_tickers() -> List[Dict[str, Any]]:
    """업비트 KRW 마켓 전 종목의 현재가 스냅샷을 한 번에 받아온다."""
    resp = requests.get(
        TICKER_ALL_URL,
        params={"quote_currencies": "KRW"},
        timeout=REQUEST_TIMEOUT_SEC,
    )
    resp.raise_for_status()
    return resp.json()


def _screening_overlay() -> Dict[str, Any]:
    """스크리닝 후보(진입 후보)와 관심 등록 목록을 티커 기준으로 찾아보기 좋게 만들어 둔다.

    스크리닝 스냅샷은 하루 한 번 돌기 때문에 순위(실시간)와 기준 시각이 다르다. 화면에서 "언제 것과
    비교한 겹침인지" 보여줄 수 있도록 스냅샷의 갱신 시각도 같이 돌려준다.

    DB가 아직 비어 있거나(최초 수집 전) 읽기에 실패해도 순위 조회 자체는 살려야 한다 — 겹침 표시는
    어디까지나 부가 정보라, 이것 때문에 페이지 전체가 죽으면 손해가 더 크다.
    """
    empty = {'by_ticker': {}, 'watchlist_tickers': set(), 'count': 0, 'updated_at': None}
    try:
        candidates = get_coin_screening_candidates()
        watchlist_tickers = get_watchlist_tickers(SCREENING_BROKER, SCREENING_MODE)
    except Exception as e:
        logger.warning(f"스크리닝 후보 겹침 정보를 읽지 못해 순위만 반환한다: {e}")
        return empty

    # 뱃지 라벨은 ENTRY_SIGNALS(진입 신호 정의의 유일한 소스)에서 그대로 가져온다 — 자동매매
    # 대시보드가 쓰는 라벨과 같아야 두 화면을 오가며 봐도 같은 말로 읽힌다.
    by_ticker = {
        c['ticker']: {'signals': [s['label'] for s in ENTRY_SIGNALS if c.get(s['key'])]}
        for c in candidates
    }
    updated_at = max((c.get('updated_at') for c in candidates if c.get('updated_at')), default=None)
    return {
        'by_ticker': by_ticker,
        'watchlist_tickers': watchlist_tickers,
        'count': len(candidates),
        'updated_at': updated_at,
    }


def _to_row(t: Dict[str, Any]) -> Dict[str, Any]:
    market = t.get('market', '')
    return {
        'ticker': market,
        'name': market.replace('KRW-', ''),
        'price': float(t.get('trade_price') or 0),
        # signed_change_rate는 0.0532 같은 비율이라 화면/로그용으로 %로 환산해 둔다.
        'change_rate': round(float(t.get('signed_change_rate') or 0) * 100, 2),
        'trade_value': float(t.get('acc_trade_price') or 0),
        'trade_value_24h': float(t.get('acc_trade_price_24h') or 0),
    }


def get_top_movers(limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    """당일 상승률 상위 / 당일 거래대금 상위를 각각 limit개씩 반환한다.

    대상은 KRW 마켓 전 종목(BTC·USDT 마켓 제외). 스테이블코인도 걸러내지 않는다 — 업비트에서는
    USDT 거래대금 자체가 시장 자금 흐름을 보는 지표라 빼 버리면 오히려 그림이 깨진다.
    """
    tickers = fetch_krw_tickers()
    rows = [_to_row(t) for t in tickers if str(t.get('market', '')).startswith('KRW-')]

    overlay = _screening_overlay()
    for r in rows:
        mark = overlay['by_ticker'].get(r['ticker'])
        r['screening_candidate'] = mark is not None
        r['signals'] = mark['signals'] if mark else []
        r['watchlisted'] = r['ticker'] in overlay['watchlist_tickers']

    top_gainers = sorted(rows, key=lambda r: r['change_rate'], reverse=True)[:limit]
    top_trade_value = sorted(rows, key=lambda r: r['trade_value'], reverse=True)[:limit]

    logger.info(f"업비트 당일 순위 조회 완료 — 대상 {len(rows)}개, 상위 {limit}개씩 반환")
    return {
        'fetched_at': datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S'),
        'total_count': len(rows),
        'top_gainers': top_gainers,
        'top_trade_value': top_trade_value,
        'screening_count': overlay['count'],
        'screening_updated_at': overlay['updated_at'],
    }



# ── 순위 이력(최근 15일) ────────────────────────────────────────────────────────
# 위의 get_top_movers()는 "지금" 한 번 보고 끝이라 어제 누가 상위였는지는 남지 않는다. 그래서
# coin-ranking-bot이 매시 RANKING_HISTORY_MINUTE분에 상위 RANKING_HISTORY_TOP개를 저장하고, 화면은
# 종목 × 날짜 표로 "며칠 들어왔는지 / 그날 마감 몇 위였는지 / 장중에만 잠깐 들어왔다 나갔는지"를 보여준다.
# 상위 목록은 종목이 계속 들고 나므로 "날짜별 목록"이 아니라 "종목별 등장 기록"으로 묶어야 비교가 된다.

HISTORY_KINDS = {
    'gainers': ('change_rate', '당일 상승률'),
    'trade_value': ('trade_value', '당일 거래대금'),
}


def capture_ranking_snapshot(now: datetime = None, tickers_fn: Callable = None, force: bool = False) -> Optional[int]:
    """이번 시(時) 스냅샷을 아직 안 찍었고 RANKING_HISTORY_MINUTE분이 지났으면 찍는다. 저장한 행 수, 건너뛰면 None.

    스냅샷을 찍은 뒤엔 보관 기간(RANKING_HISTORY_KEEP_DAYS)보다 오래된 날짜를 지운다.
    """
    now = now or datetime.now(KST)
    date, hour = now.strftime('%Y-%m-%d'), now.hour
    if not force and (now.minute < Config.RANKING_HISTORY_MINUTE or has_coin_ranking_snapshot(date, hour)):
        return None

    tickers = (tickers_fn or fetch_krw_tickers)()
    rows = [_to_row(t) for t in tickers if str(t.get('market', '')).startswith('KRW-')]
    top = Config.RANKING_HISTORY_TOP
    ranked = {kind: sorted(rows, key=lambda r: r[field], reverse=True)[:top]
              for kind, (field, _) in HISTORY_KINDS.items()}
    saved = save_coin_ranking_snapshot(date, hour, now.strftime('%Y-%m-%d %H:%M:%S'), ranked)

    keep_from = (now - timedelta(days=Config.RANKING_HISTORY_KEEP_DAYS - 1)).strftime('%Y-%m-%d')
    purged = delete_coin_ranking_before(keep_from)
    logger.info(f"코인 순위 이력 저장 — {date} {hour:02d}시 {saved}행" + (f", 오래된 {purged}행 삭제" if purged else ''))
    return saved


def run_ranking_history_loop(poll_sec: int = 60):
    """1분마다 깨어나 이번 시 스냅샷이 필요한지만 확인한다(대부분은 DB 조회 1번으로 끝)."""
    logger.info(f"코인 순위 이력 루프 시작 — 매시 {Config.RANKING_HISTORY_MINUTE}분, "
                f"상위 {Config.RANKING_HISTORY_TOP}개, {Config.RANKING_HISTORY_KEEP_DAYS}일 보관")
    while True:
        try:
            capture_ranking_snapshot()
        except Exception as e:
            logger.error(f"코인 순위 이력 저장 오류: {e}")
        time.sleep(poll_sec)


def get_ranking_history(kind: str = 'gainers', top: int = 10, days: int = None, now: datetime = None) -> Dict[str, Any]:
    """최근 days일 동안 kind 상위 top 안에 한 번이라도 든 종목을 종목별로 묶어 돌려준다.

    날짜별 셀:
      - close_rank : 그날 마지막 스냅샷(보통 23시 = 마감 직전) 순위. top 밖이면 None
      - best_rank  : 그날 장중 최고 순위(top 안일 때만)
      - hours_in   : 그날 top 안에 있던 스냅샷 수(= 시간 수) / hours_total: 그날 찍힌 스냅샷 수
    오늘은 아직 안 끝났으므로 close_rank가 "지금까지 마지막 스냅샷" 기준이다(close_hour로 구분).
    """
    if kind not in HISTORY_KINDS:
        raise ValueError(f"알 수 없는 순위 종류: {kind}")
    days = days or Config.RANKING_HISTORY_KEEP_DAYS
    now = now or datetime.now(KST)
    since = (now - timedelta(days=days - 1)).strftime('%Y-%m-%d')
    snaps = get_coin_ranking_history(kind, since)

    by_date: Dict[str, Dict[int, List[Dict[str, Any]]]] = {}
    for r in snaps:
        by_date.setdefault(r['date'], {}).setdefault(r['hour'], []).append(r)
    dates = sorted(by_date, reverse=True)  # 최신 날짜가 왼쪽

    coins: Dict[str, Dict[str, Any]] = {}
    date_meta = []
    for d in dates:
        hours = by_date[d]
        close_hour = max(hours)
        date_meta.append({'date': d, 'close_hour': close_hour, 'snapshots': len(hours)})
        for hour, rows in hours.items():
            for r in rows:
                if r['rank'] > top:
                    continue
                c = coins.setdefault(r['ticker'], {'ticker': r['ticker'], 'name': r['ticker'].replace('KRW-', ''),
                                                   'days': {}})
                cell = c['days'].setdefault(d, {'close_rank': None, 'best_rank': None, 'hours_in': 0,
                                                'hours_total': len(hours), 'close_change_rate': None})
                cell['hours_in'] += 1
                cell['best_rank'] = r['rank'] if cell['best_rank'] is None else min(cell['best_rank'], r['rank'])
                if hour == close_hour:
                    cell['close_rank'] = r['rank']
                    cell['close_change_rate'] = r['change_rate']

    latest = dates[0] if dates else None
    latest_hour = date_meta[0]['close_hour'] if date_meta else None
    result = []
    for c in coins.values():
        cells = c['days']
        c['days_in'] = len(cells)                                    # 장중 한 번이라도 top 안
        c['days_close_in'] = sum(1 for v in cells.values() if v['close_rank'] is not None)  # 마감에도 top 안
        c['best_rank'] = min(v['best_rank'] for v in cells.values())
        c['last_seen'] = max(cells)
        c['now_in'] = latest is not None and (cells.get(latest) or {}).get('close_rank') is not None
        result.append(c)
    result.sort(key=lambda c: (-c['days_in'], -c['days_close_in'], c['best_rank'], c['ticker']))

    return {
        'kind': kind,
        'kind_label': HISTORY_KINDS[kind][1],
        'top': top,
        'days': days,
        'dates': date_meta,
        'latest': {'date': latest, 'hour': latest_hour} if latest else None,
        'coins': result,
    }


def _format_table(title: str, rows: List[Dict[str, Any]]) -> str:
    lines = [title, f"{'순위':<4}{'종목':<10}{'현재가':>14}{'등락률':>9}{'거래대금(억)':>14}  스크리닝"]
    for i, r in enumerate(rows, 1):
        mark = ' / '.join(r.get('signals') or []) if r.get('screening_candidate') else '-'
        if r.get('watchlisted'):
            mark += ' (관심등록)'
        lines.append(
            f"{i:<4}{r['name']:<10}{r['price']:>14,.2f}{r['change_rate']:>8.2f}%"
            f"{r['trade_value'] / 1e8:>14,.0f}  {mark}"
        )
    return "\n".join(lines)


if __name__ == '__main__':
    result = get_top_movers()
    print(f"기준 시각 {result['fetched_at']} (KST) · KRW 마켓 {result['total_count']}개")
    if result['screening_updated_at']:
        print(f"스크리닝 후보 {result['screening_count']}개 (스냅샷 {result['screening_updated_at']})")
    else:
        print("스크리닝 스냅샷이 없어 겹침 표시를 건너뜁니다 — 코인 스크리닝을 먼저 수집하세요.")
    print()
    print(_format_table("📈 당일 상승률 상위 10", result['top_gainers']))
    print()
    print(_format_table("💰 당일 거래대금 상위 10", result['top_trade_value']))
