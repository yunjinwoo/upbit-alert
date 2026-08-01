import time
from datetime import datetime
import pyupbit
import pandas as pd
from app.config import Config
from app.utils.logger import get_logger
from app.utils.db_manager import save_coin_screening, save_job_run_log

logger = get_logger()

INTERVAL = "minute240"          # 4시간봉
CANDLE_COUNT = 230              # MA200 + 일목균형표(52+26) 계산에 충분한 여유치
ICHIMOKU_TENKAN = 9
ICHIMOKU_KIJUN = 26
ICHIMOKU_SENKOU_B = 52
MA200_PERIOD = 200

VOL_RATIO_THRESHOLD = Config.UPBIT_THRESHOLDS["minutes240"]  # 기존 거래량 급증 감시와 동일 임계값 재사용


def calc_indicators(df: pd.DataFrame) -> dict:
    """4시간봉 OHLCV DataFrame으로 돌파/구름/200선 근접 여부를 계산한다.
    df의 마지막 행(-1)은 아직 진행 중인 캔들이므로, 모든 계산은 마지막 확정 캔들(-2) 기준.
    데이터가 부족해 계산할 수 없는 지표는 None/False로 채운다."""
    n = len(df)
    result = {
        'ma200': None, 'ma200_dist_pct': None, 'near_ma200': False,
        'above_cloud': False,
        'breakout_4h': False, 'breakout_vol_ratio': None, 'breakout_candle_rate': None,
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
        result['ma200'] = round(float(ma200), 6)
        result['ma200_dist_pct'] = round(float(dist_pct), 2)
        result['near_ma200'] = bool(abs(dist_pct) <= Config.COIN_MA200_NEAR_PCT)

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

    # ── 4시간봉 돌파 (거래량 급증 + 상승 + "처음") 여부
    lookback = Config.COIN_BREAKOUT_VOL_LOOKBACK
    if idx_now - 1 - lookback >= 0:
        avg_vol_now = volumes.iloc[idx_now - lookback: idx_now].mean()
        avg_vol_prev = volumes.iloc[idx_now - 1 - lookback: idx_now - 1].mean()
        vol_ratio_now = volumes.iloc[idx_now] / avg_vol_now if avg_vol_now else 0
        vol_ratio_prev = volumes.iloc[idx_now - 1] / avg_vol_prev if avg_vol_prev else 0

        candle_open = df['open'].iloc[idx_now]
        candle_rate = (close_now - candle_open) / candle_open * 100 if candle_open else 0

        result['breakout_vol_ratio'] = round(float(vol_ratio_now), 2)
        result['breakout_candle_rate'] = round(float(candle_rate), 2)
        result['breakout_4h'] = bool(
            vol_ratio_now >= VOL_RATIO_THRESHOLD
            and candle_rate >= Config.COIN_BREAKOUT_RATE_THRESHOLD
            and vol_ratio_prev < VOL_RATIO_THRESHOLD
        )

    return result


def run_coin_screening(trigger_type: str = 'auto'):
    """전체 KRW 마켓 코인의 4시간봉 데이터로 매매 후보 필터 지표를 계산해 DB에 저장한다.
    실행 시각/결과는 job_run_log에도 남겨서 "언제 다시 수집됐는지" 이력을 동기화 관리 페이지에서 확인할 수 있게 한다."""
    logger.info("코인 스크리닝 시작 (Upbit, 4시간봉)")
    start = datetime.now()
    error_message = None
    tickers = pyupbit.get_tickers(fiat="KRW")

    rows = []
    for i, ticker in enumerate(tickers, 1):
        try:
            df = pyupbit.get_ohlcv(ticker, interval=INTERVAL, count=CANDLE_COUNT)
            if df is None or len(df) < 8:
                continue

            indicators = calc_indicators(df)

            idx_now = len(df) - 2
            close_now = df['close'].iloc[idx_now]
            close_24h_ago = df['close'].iloc[idx_now - 6] if idx_now - 6 >= 0 else df['open'].iloc[0]
            change_rate = (close_now - close_24h_ago) / close_24h_ago * 100 if close_24h_ago else 0.0
            trade_value_24h = df['value'].iloc[max(0, idx_now - 5): idx_now + 1].sum()

            rows.append({
                'ticker': ticker,
                'name': ticker.replace('KRW-', ''),
                'price': float(close_now),
                'change_rate': round(float(change_rate), 2),
                'trade_value': float(trade_value_24h),
                **indicators,
            })
        except Exception as e:
            logger.error(f"[{ticker}] 스크리닝 지표 계산 실패: {e}")

        time.sleep(0.15)

        if i % 30 == 0:
            logger.info(f"코인 스크리닝 진행: {i}/{len(tickers)}")

    save_coin_screening(rows)
    logger.info(f"코인 스크리닝 완료: {len(rows)}개 종목 저장")

    end = datetime.now()
    save_job_run_log(
        'coin_screening', '코인 스크리닝(매매 후보 필터) 수집', 'pyupbit get_ohlcv(minute240)',
        start.strftime('%Y-%m-%d %H:%M:%S'), end.strftime('%Y-%m-%d %H:%M:%S'),
        success=True, count=len(rows), error_message=error_message, trigger_type=trigger_type,
    )
    return len(rows)


def run_coin_screening_loop(interval_sec: int = 1800):
    while True:
        try:
            run_coin_screening(trigger_type='auto')
        except Exception as e:
            logger.error(f"코인 스크리닝 루프 오류: {e}")
        time.sleep(interval_sec)


if __name__ == "__main__":
    run_coin_screening_loop()
