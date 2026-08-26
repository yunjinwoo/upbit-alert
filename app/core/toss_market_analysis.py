"""토스증권(국내주식) 매매 후보 필터 스크리닝 — app/core/upbit_market_analysis.py와 동일한 지표
(200이평선 근접/일목구름 위/거래량 급증 돌파)를 코인의 4시간봉 대신 일봉(1d) 기준으로 계산한다.
calc_indicators()는 OHLCV DataFrame이면 봉 종류와 무관하게 동작하는 로직이라 그대로 이식했다
(필드명만 breakout_4h → breakout_1d).

유니버스는 전체 KRX를 매번 스캔하지 않고, 이미 주기적으로 수집되는 시가총액 랭킹
(stock_market_cap_daily, app/core/stock_monitor.run_job_market_cap_and_signal_score)의 최신
상위 N종목(Config.TOSS_SCREENING_UNIVERSE_SIZE)만 대상으로 한다 — 이 랭킹 잡이 최소 한 번은
돌아 있어야(코스피/코스닥) 이 스크리닝의 후보가 채워진다.
"""
import time

import pandas as pd

from app.config import Config
from app.utils.logger import get_logger
from app.core.toss_client import get_daily_candles_extended
from app.utils.db_manager import save_stock_screening, get_latest_market_cap_date, get_stock_investor_combined

logger = get_logger()

CANDLE_COUNT = 260               # MA200 + 일목균형표(52+26) 계산에 충분한 여유치
ICHIMOKU_TENKAN = 9
ICHIMOKU_KIJUN = 26
ICHIMOKU_SENKOU_B = 52
MA200_PERIOD = 200

# 일봉은 4시간봉보다 하루 변동폭이 커서 거래량 급증 기준(배수)을 코인 스크리닝(3.0배)보다 낮게 잡음.
# 돌파 캔들 자체 등락률 기준은 코인 스크리닝과 동일 값을 재사용.
BREAKOUT_VOL_RATIO_THRESHOLD = 2.0
BREAKOUT_RATE_THRESHOLD = Config.COIN_BREAKOUT_RATE_THRESHOLD
BREAKOUT_VOL_LOOKBACK = Config.COIN_BREAKOUT_VOL_LOOKBACK

# 세 번째 후보 조건("모멘텀 컨플루언스") — app/core/upbit_market_analysis.py와 동일(200선 위 + EMA
# 5/20/60 골든크로스 + RSI<70 + MACD 히스토그램 양수), 일봉 기준으로 계산.
EMA_SHORT, EMA_MID, EMA_LONG = 5, 20, 60
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float('nan'))
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(100)


def _macd_histogram(series: pd.Series, fast: int, slow: int, signal: int) -> pd.Series:
    macd_line = _ema(series, fast) - _ema(series, slow)
    signal_line = _ema(macd_line, signal)
    return macd_line - signal_line


def calc_indicators(df: pd.DataFrame) -> dict:
    """일봉 OHLCV DataFrame으로 돌파/구름/200선 근접 여부를 계산한다.
    df의 마지막 행(-1)은 아직 진행 중인(오늘) 캔들이므로, 모든 계산은 마지막 확정 캔들(-2) 기준.
    데이터가 부족해 계산할 수 없는 지표는 None/False로 채운다."""
    n = len(df)
    result = {
        'ma200': None, 'ma200_dist_pct': None, 'near_ma200': False,
        'above_cloud': False,
        'breakout_1d': False, 'breakout_vol_ratio': None, 'breakout_candle_rate': None,
        'momentum_confluence': False,
    }
    if n < 3:
        return result

    closes = df['close']
    highs = df['high']
    lows = df['low']
    volumes = df['volume']

    idx_now = n - 2   # 마지막 확정 캔들
    close_now = closes.iloc[idx_now]

    # ── 200이동평균선 근접 여부
    if idx_now + 1 >= MA200_PERIOD:
        ma200 = closes.iloc[idx_now - MA200_PERIOD + 1: idx_now + 1].mean()
        dist_pct = (close_now - ma200) / ma200 * 100
        result['ma200'] = round(float(ma200), 2)
        result['ma200_dist_pct'] = round(float(dist_pct), 2)
        result['near_ma200'] = bool(abs(dist_pct) <= Config.COIN_MA200_NEAR_PCT)

        # ── 모멘텀 컨플루언스: 200선 위 + EMA 5/20/60 골든크로스(이번 캔들에 막 교차) + RSI<70 +
        # MACD 히스토그램 양수. idx_now+1>=200이 이미 보장돼 있어 EMA60/RSI14/MACD(12,26,9) 워밍업은
        # 항상 충분하다.
        ema_short = _ema(closes, EMA_SHORT)
        ema_mid = _ema(closes, EMA_MID)
        ema_long = _ema(closes, EMA_LONG)
        golden_cross = bool(
            ema_short.iloc[idx_now] > ema_mid.iloc[idx_now]
            and ema_short.iloc[idx_now - 1] <= ema_mid.iloc[idx_now - 1]
            and ema_mid.iloc[idx_now] > ema_long.iloc[idx_now]
        )
        rsi_ok = bool(_rsi(closes, RSI_PERIOD).iloc[idx_now] < RSI_OVERBOUGHT)
        macd_ok = bool(_macd_histogram(closes, MACD_FAST, MACD_SLOW, MACD_SIGNAL).iloc[idx_now] > 0)
        result['momentum_confluence'] = bool(close_now > ma200) and golden_cross and rsi_ok and macd_ok

    # ── 일목균형표 구름 위 여부 (26봉 전에 계산된 선행스팬을 지금 캔들과 비교)
    cloud_idx = idx_now - ICHIMOKU_KIJUN
    if cloud_idx - ICHIMOKU_SENKOU_B + 1 >= 0:
        def donchian_mid(period, end_idx):
            window_high = highs.iloc[end_idx - period + 1: end_idx + 1].max()
            window_low = lows.iloc[end_idx - period + 1: end_idx + 1].min()
            return (window_high + window_low) / 2

        tenkan = donchian_mid(ICHIMOKU_TENKAN, cloud_idx)
        kijun = donchian_mid(ICHIMOKU_KIJUN, cloud_idx)
        senkou_a = (tenkan + kijun) / 2
        senkou_b = donchian_mid(ICHIMOKU_SENKOU_B, cloud_idx)
        cloud_top = max(senkou_a, senkou_b)
        result['above_cloud'] = bool(close_now > cloud_top)

    # ── 일봉 돌파(거래량 급증 + 상승 + "처음") 여부
    lookback = BREAKOUT_VOL_LOOKBACK
    if idx_now - 1 - lookback >= 0:
        avg_vol_now = volumes.iloc[idx_now - lookback: idx_now].mean()
        avg_vol_prev = volumes.iloc[idx_now - 1 - lookback: idx_now - 1].mean()
        vol_ratio_now = volumes.iloc[idx_now] / avg_vol_now if avg_vol_now else 0
        vol_ratio_prev = volumes.iloc[idx_now - 1] / avg_vol_prev if avg_vol_prev else 0

        candle_open = df['open'].iloc[idx_now]
        candle_rate = (close_now - candle_open) / candle_open * 100 if candle_open else 0

        result['breakout_vol_ratio'] = round(float(vol_ratio_now), 2)
        result['breakout_candle_rate'] = round(float(candle_rate), 2)
        result['breakout_1d'] = bool(
            vol_ratio_now >= BREAKOUT_VOL_RATIO_THRESHOLD
            and candle_rate >= BREAKOUT_RATE_THRESHOLD
            and vol_ratio_prev < BREAKOUT_VOL_RATIO_THRESHOLD
        )

    return result


def run_stock_screening():
    """시가총액 상위 N종목의 일봉 데이터로 매매 후보 필터 지표를 계산해 DB에 저장한다."""
    logger.info("국내주식 스크리닝 시작 (토스, 일봉)")
    date = get_latest_market_cap_date()
    if not date:
        logger.warning("시가총액 랭킹 데이터가 없어 스크리닝을 건너뜁니다 "
                        "(stock/coin_analysis 프로세스가 먼저 market_cap을 채워야 함).")
        return 0

    universe = get_stock_investor_combined(date, "combined")[:Config.TOSS_SCREENING_UNIVERSE_SIZE]

    rows = []
    for i, item in enumerate(universe, 1):
        code = item['code']
        name = item.get('name')
        try:
            df = get_daily_candles_extended(code, count=CANDLE_COUNT)
            if df is None or len(df) < 8:
                continue

            indicators = calc_indicators(df)

            idx_now = len(df) - 2
            close_now = df['close'].iloc[idx_now]
            close_prev = df['close'].iloc[idx_now - 1] if idx_now - 1 >= 0 else df['open'].iloc[0]
            change_rate = (close_now - close_prev) / close_prev * 100 if close_prev else 0.0
            trade_value = float(close_now * df['volume'].iloc[idx_now])

            rows.append({
                'ticker': code,
                'name': name,
                'price': float(close_now),
                'change_rate': round(float(change_rate), 2),
                'trade_value': trade_value,
                **indicators,
            })
        except Exception as e:
            logger.error(f"[{code}] 스크리닝 지표 계산 실패: {e}")

        time.sleep(0.1)  # 토스 API rate limit(MARKET_DATA_CHART 20회/초) 대비 여유

        if i % 30 == 0:
            logger.info(f"국내주식 스크리닝 진행: {i}/{len(universe)}")

    save_stock_screening(rows)
    logger.info(f"국내주식 스크리닝 완료: {len(rows)}개 종목 저장")
    return len(rows)


def run_stock_screening_loop(interval_sec: int = 1800):
    while True:
        try:
            run_stock_screening()
        except Exception as e:
            logger.error(f"국내주식 스크리닝 루프 오류: {e}")
        time.sleep(interval_sec)


if __name__ == "__main__":
    run_stock_screening_loop()
