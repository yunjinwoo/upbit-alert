"""수렴 자동 매수(docs/auto-trade-convergence.md) — 사용자 승인 없이 봇이 직접 종목을 고른다.

24시간 거래대금이 기준(기본 400억) 이상인 KRW 코인 중에서
  - 지금 안 갖고 있고 최근 N일(기본 7일) 봇 매매 이력도 없는 "새 코인"이고
  - 5분봉 현재가·일목 구름(상단/하단)·MA80·MA120의 최고값과 최저값 차이가 max_gap_pct(기본 1.5%) 이내이며
  - (require_above) 5분봉 현재가가 구름 위·MA120 위이고
  - (require_daily_trend) 일봉 현재가가 구름 위·기준선(26) 위인
코인을 하루(KST 0시 기준) daily_limit개(기본 1개)까지 산다. 여러 개가 동시에 맞으면 차이(gap)가 작은 순.
산 뒤에는 일반 종목처럼 손절/익절/RSI 등 기존 청산 규칙을 탄다.

판정(measure_*/judge_row/pick_buys)은 네트워크 없는 순수 함수이고, 캔들 조회는 scan_market()에서만 한다.
"""
from typing import Callable, List, Optional

import pandas as pd
import pyupbit

from app.config import Config
from app.core.trade_strategy import TradeDecision
from app.core.upbit_market_analysis import ICHIMOKU_KIJUN, cloud_levels
from app.core.upbit_ranking import fetch_krw_tickers
from app.utils.db_manager import CONVERGENCE_REASON_PREFIX
from app.utils.logger import get_logger

logger = get_logger()

CANDLE_COUNT_M5 = 200   # MA120 + 구름(KIJUN 26 + SENKOU_B 52)에 충분한 개수
CANDLE_COUNT_1D = 90


def measure_convergence(df: Optional[pd.DataFrame], ma_short: int = None, ma_long: int = None) -> Optional[dict]:
    """5분봉 마지막 확정 캔들 기준 현재가·구름·MA80·MA120과 그 최고/최저 차이(%). 캔들이 모자라면 None."""
    ma_short = ma_short or Config.CONVERGENCE_MA_SHORT
    ma_long = ma_long or Config.CONVERGENCE_MA_LONG
    if df is None or len(df) < ma_long + 2:
        return None
    idx = len(df) - 2
    levels = cloud_levels(df, idx)
    if levels is None:
        return None
    close = df['close']
    price = float(close.iloc[idx])
    ma_s = float(close.iloc[idx - ma_short + 1: idx + 1].mean())
    ma_l = float(close.iloc[idx - ma_long + 1: idx + 1].mean())
    cloud_top, cloud_bottom = (float(v) for v in levels)
    values = [price, cloud_top, cloud_bottom, ma_s, ma_l]
    low = min(values)
    return {
        'price': price, 'ma_short': ma_s, 'ma_long': ma_l, 'cloud_top': cloud_top, 'cloud_bottom': cloud_bottom,
        'gap_pct': (max(values) - low) / low * 100 if low > 0 else None,
        'above': bool(price > cloud_top and price > ma_l),
    }


def measure_daily_trend(df: Optional[pd.DataFrame]) -> Optional[dict]:
    """일봉 현재가(진행 중 캔들 종가)가 구름 위·기준선(26) 위인지. 캔들이 모자라면 None.
    일봉은 확정 캔들이 어제 것이라 하루 늦으므로, 5분봉과 달리 진행 중 캔들로 본다."""
    if df is None or len(df) < ICHIMOKU_KIJUN:
        return None
    idx = len(df) - 1
    levels = cloud_levels(df, idx)
    if levels is None:
        return None
    window = df.iloc[idx - ICHIMOKU_KIJUN + 1: idx + 1]
    kijun = float((window['high'].max() + window['low'].min()) / 2)
    price = float(df['close'].iloc[idx])
    return {'price': price, 'kijun': kijun, 'cloud_top': float(levels[0]),
            'ok': bool(price > levels[0] and price > kijun)}


def judge_row(row: dict, settings: dict) -> Optional[str]:
    """scan_market()이 만든 한 줄을 판정 — 통과면 None, 아니면 떨어진 이유."""
    if row.get('excluded'):
        return row['excluded']
    if row.get('gap_pct') is None:
        return '5분봉 캔들 부족/조회 실패'
    if row['gap_pct'] > settings['max_gap_pct']:
        return f"이격 {row['gap_pct']:.2f}% > {settings['max_gap_pct']:g}%"
    if settings['require_above'] and not row.get('above'):
        return '5분봉 구름·120선 아래'
    if settings['require_daily_trend']:
        if row.get('daily_ok') is None:
            return '일봉 캔들 부족/조회 실패'
        if not row['daily_ok']:
            return '일봉 구름·기준선 아래'
    return None


def pick_buys(rows: List[dict], remaining: int, amount_krw: float, cash_balance: float) -> List[TradeDecision]:
    """통과한 줄(reason 없음) 중 이격이 작은 순으로 remaining개까지, 현금이 되는 만큼 BUY."""
    decisions = []
    for row in sorted((r for r in rows if not r.get('reason')), key=lambda r: r['gap_pct']):
        if len(decisions) >= remaining or cash_balance < amount_krw:
            break
        decisions.append(TradeDecision(
            row['ticker'], 'BUY', price=row.get('price'), amount_krw=amount_krw,
            reason=(f"{CONVERGENCE_REASON_PREFIX}(이격 {row['gap_pct']:.2f}%, "
                    f"24h 거래대금 {row['trade_value_24h'] / 1e8:,.0f}억)"),
        ))
        cash_balance -= amount_krw
    return decisions


def _get_candles(ticker: str, interval: str, count: int):
    try:
        return pyupbit.get_ohlcv(ticker, interval=interval, count=count)
    except Exception as e:
        logger.error(f"[수렴매수] {ticker} {interval} 캔들 조회 실패: {e}")
        return None


def scan_market(settings: dict, excluded: dict, fetch_tickers: Callable = fetch_krw_tickers,
                get_candles: Callable = _get_candles) -> List[dict]:
    """거래대금 기준을 넘는 KRW 코인을 훑어 한 줄씩 판정 결과(reason: None=통과)를 붙여 돌려준다.
    excluded: {ticker: 제외 이유} — 보유 중/최근 매매 등. 이 코인들은 캔들을 받지 않는다.
    일봉은 5분봉 조건을 통과한 코인만 받는다(호출 수 절약)."""
    rows = []
    for t in fetch_tickers():
        ticker = str(t.get('market', ''))
        value = float(t.get('acc_trade_price_24h') or 0)
        if not ticker.startswith('KRW-') or value < settings['min_trade_value_24h']:
            continue
        row = {'ticker': ticker, 'trade_value_24h': value, 'gap_pct': None, 'above': None, 'daily_ok': None}
        if ticker in Config.CONVERGENCE_EXCLUDE_TICKERS:
            row['excluded'] = '스테이블코인'
        elif ticker in excluded:
            row['excluded'] = excluded[ticker]
        else:
            m5 = measure_convergence(get_candles(ticker, 'minute5', CANDLE_COUNT_M5))
            if m5:
                row.update(price=m5['price'], gap_pct=round(m5['gap_pct'], 3), above=m5['above'])
            if settings['require_daily_trend'] and judge_row(row, {**settings, 'require_daily_trend': False}) is None:
                daily = measure_daily_trend(get_candles(ticker, 'day', CANDLE_COUNT_1D))
                row['daily_ok'] = daily['ok'] if daily else None
        row['reason'] = judge_row(row, settings)
        row.pop('excluded', None)
        rows.append(row)
    rows.sort(key=lambda r: (r['reason'] is not None, r['gap_pct'] if r['gap_pct'] is not None else 1e9))
    return rows
