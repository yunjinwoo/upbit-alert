"""업비트 KRW 마켓 당일 순위 조회 — 상승률 상위 / 거래대금 상위.

기존 코인 스크리닝(app/core/upbit_market_analysis.py)은 종목마다 캔들을 받아 지표를 계산하느라
2~4분이 걸린다. 여기서는 "지금 뭐가 오르고 뭐에 돈이 몰렸나"만 보면 되므로 업비트의 전 종목 현재가
엔드포인트(/v1/ticker/all) 한 번으로 끝낸다 — 호출 1회, 응답 즉시라 화면에서 바로 새로고침해도 된다.

기준(업비트 화면과 동일):
  - 당일 등락률   signed_change_rate — 전일 종가(KST 00시) 대비 현재가
  - 당일 거래대금 acc_trade_price    — KST 00시부터 누적된 원화 거래대금
    (acc_trade_price_24h는 최근 24시간 기준이라 별도 필드로 같이 담아 둔다)
"""
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

import requests

from app.utils.logger import get_logger

logger = get_logger()

TICKER_ALL_URL = "https://api.upbit.com/v1/ticker/all"
REQUEST_TIMEOUT_SEC = 10
DEFAULT_LIMIT = 10
KST = timezone(timedelta(hours=9))


def fetch_krw_tickers() -> List[Dict[str, Any]]:
    """업비트 KRW 마켓 전 종목의 현재가 스냅샷을 한 번에 받아온다."""
    resp = requests.get(
        TICKER_ALL_URL,
        params={"quote_currencies": "KRW"},
        timeout=REQUEST_TIMEOUT_SEC,
    )
    resp.raise_for_status()
    return resp.json()


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

    top_gainers = sorted(rows, key=lambda r: r['change_rate'], reverse=True)[:limit]
    top_trade_value = sorted(rows, key=lambda r: r['trade_value'], reverse=True)[:limit]

    logger.info(f"업비트 당일 순위 조회 완료 — 대상 {len(rows)}개, 상위 {limit}개씩 반환")
    return {
        'fetched_at': datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S'),
        'total_count': len(rows),
        'top_gainers': top_gainers,
        'top_trade_value': top_trade_value,
    }


def _format_table(title: str, rows: List[Dict[str, Any]]) -> str:
    lines = [title, f"{'순위':<4}{'종목':<10}{'현재가':>14}{'등락률':>9}{'거래대금(억)':>14}"]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{i:<4}{r['name']:<10}{r['price']:>14,.2f}{r['change_rate']:>8.2f}%"
            f"{r['trade_value'] / 1e8:>14,.0f}"
        )
    return "\n".join(lines)


if __name__ == '__main__':
    result = get_top_movers()
    print(f"기준 시각 {result['fetched_at']} (KST) · KRW 마켓 {result['total_count']}개")
    print()
    print(_format_table("📈 당일 상승률 상위 10", result['top_gainers']))
    print()
    print(_format_table("💰 당일 거래대금 상위 10", result['top_trade_value']))
