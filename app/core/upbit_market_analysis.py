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

# 돌파(breakout_4h)/구름위(above_cloud)와 같은 판정을 일봉으로도 병행 계산하기 위한 별도
# 타임프레임/임계값. 200선(MA200)은 4시간봉만 유지한다(200개 캔들 필요 — 속도상 이유로 제외).
INTERVAL_1D = "day"
CANDLE_COUNT_1D = 90             # 일목구름(KIJUN 26 + SENKOU_B 52 + 여유치) 계산에 충분한 개수.
                                 # 돌파 판정(lookback*2)은 이보다 훨씬 적게 필요해 따로 늘릴 필요 없음.
VOL_RATIO_THRESHOLD_1D = Config.UPBIT_THRESHOLDS["day"]  # 실시간 감시 일봉 임계값과 동일 소스 재사용

# 세 번째 후보 조건("모멘텀 컨플루언스") — 200선 위 + EMA 5/20/60 골든크로스 + RSI<70 + MACD 히스토그램
# 양수, 4가지가 전부 같은 캔들에서 맞아야 통과. 4시간봉 기준으로 계산(기존 200선/구름 계산과 동일 캔들
# 재사용 — 네트워크 호출을 추가로 늘리지 않기 위함). 유튜브 등에서 흔히 보는 "3중 EMA 골든크로스 +
# RSI + MACD 확인" 스타일 진입 신호를 이 저장소의 기존 지표 계산 파이프라인에 이식한 것.
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
    return rsi.fillna(100)  # avg_loss가 0(구간 내내 상승만)이면 RSI=100으로 취급


def _macd_histogram(series: pd.Series, fast: int, slow: int, signal: int) -> pd.Series:
    macd_line = _ema(series, fast) - _ema(series, slow)
    signal_line = _ema(macd_line, signal)
    return macd_line - signal_line


def _calc_breakout(df: pd.DataFrame, vol_ratio_threshold: float, rate_threshold: float, lookback: int) -> dict:
    """OHLCV DataFrame으로 "거래량 급증 + 상승 + 처음" 돌파 여부를 계산한다 — 봉 종류(4시간봉/일봉)
    무관하게 동작. df의 마지막 행(-1)은 진행 중인 캔들이므로 계산은 마지막 확정 캔들(-2) 기준.
    lookback*2개 이상의 확정 캔들이 없으면 판정 불가로 보고 False/None을 반환한다."""
    result = {'breakout': False, 'vol_ratio': None, 'candle_rate': None}
    idx_now = len(df) - 2
    if idx_now - 1 - lookback < 0:
        return result

    volumes = df['volume']
    closes = df['close']

    avg_vol_now = volumes.iloc[idx_now - lookback: idx_now].mean()
    avg_vol_prev = volumes.iloc[idx_now - 1 - lookback: idx_now - 1].mean()
    vol_ratio_now = volumes.iloc[idx_now] / avg_vol_now if avg_vol_now else 0
    vol_ratio_prev = volumes.iloc[idx_now - 1] / avg_vol_prev if avg_vol_prev else 0

    candle_open = df['open'].iloc[idx_now]
    close_now = closes.iloc[idx_now]
    candle_rate = (close_now - candle_open) / candle_open * 100 if candle_open else 0

    result['vol_ratio'] = round(float(vol_ratio_now), 2)
    result['candle_rate'] = round(float(candle_rate), 2)
    result['breakout'] = bool(
        vol_ratio_now >= vol_ratio_threshold
        and candle_rate >= rate_threshold
        and vol_ratio_prev < vol_ratio_threshold
    )
    return result


def _calc_cloud(df: pd.DataFrame) -> dict:
    """OHLCV DataFrame으로 일목균형표 구름 위/아래 여부를 계산한다 — 봉 종류(4시간봉/일봉) 무관하게
    동작. 26봉(KIJUN) 전에 계산된 선행스팬을 지금 캔들과 비교하는 방식이라 KIJUN+SENKOU_B(78봉)
    이상의 확정 캔들이 없으면 판정 불가로 보고 False를 반환한다."""
    result = {'above_cloud': False, 'below_cloud': False}
    idx_now = len(df) - 2
    cloud_idx = idx_now - ICHIMOKU_KIJUN
    if cloud_idx - ICHIMOKU_SENKOU_B + 1 < 0:
        return result

    highs = df['high']
    lows = df['low']
    close_now = df['close'].iloc[idx_now]

    def donchian_mid(period, end_idx):
        window_high = highs.iloc[end_idx - period + 1: end_idx + 1].max()
        window_low = lows.iloc[end_idx - period + 1: end_idx + 1].min()
        return (window_high + window_low) / 2

    tenkan = donchian_mid(ICHIMOKU_TENKAN, cloud_idx)
    kijun = donchian_mid(ICHIMOKU_KIJUN, cloud_idx)
    senkou_a = (tenkan + kijun) / 2
    senkou_b = donchian_mid(ICHIMOKU_SENKOU_B, cloud_idx)
    cloud_top = max(senkou_a, senkou_b)
    cloud_bottom = min(senkou_a, senkou_b)
    result['above_cloud'] = bool(close_now > cloud_top)
    result['below_cloud'] = bool(close_now < cloud_bottom)
    return result


def calc_indicators(df: pd.DataFrame) -> dict:
    """4시간봉 OHLCV DataFrame으로 돌파/구름/200선 근접 여부를 계산한다.
    df의 마지막 행(-1)은 아직 진행 중인 캔들이므로, 모든 계산은 마지막 확정 캔들(-2) 기준.
    데이터가 부족해 계산할 수 없는 지표는 None/False로 채운다."""
    n = len(df)
    result = {
        'ma200': None, 'ma200_dist_pct': None, 'near_ma200': False,
        'above_cloud': False, 'above_cloud_1d': False,
        'breakout_4h': False, 'breakout_vol_ratio': None, 'breakout_candle_rate': None,
        'breakout_1d': False, 'breakout_1d_vol_ratio': None, 'breakout_1d_candle_rate': None,
        'momentum_confluence': False,
        # ── 하락위험(Downside Watch) 신호 — 진입 3신호의 반대편. docs/auto-trade-downside-watch.md
        'below_ma200': False,      # 종가가 200선 대비 -COIN_MA200_NEAR_PCT 아래(명확한 이탈)
        'below_cloud': False,      # 종가가 일목균형표 구름 하단 아래
        'ema_dead_cross': False,   # EMA5가 EMA20 하향 돌파(이번 캔들) 또는 EMA20<EMA60 역배열
        'macd_neg': False,         # MACD 히스토그램 음수
        'rsi_overbought': False,   # RSI > 70 (뱃지/필터 전용 — 단독으로 목록 등재는 안 함)
        'rsi': None, 'macd_hist': None,
        # ── 최근 N개 4시간봉 중 RSI가 임계값을 넘은 적 있는지 — 모멘텀 과열 스크리닝 필터 전용
        # (coin_screening.html에서만 사용, 자동매매 진입 조건에는 포함하지 않음)
        'rsi_recent_breakout': False, 'rsi_recent_breakout_max': None,
    }
    if n < 3:
        return result

    closes = df['close']

    idx_now = n - 2   # 마지막 확정 캔들
    close_now = closes.iloc[idx_now]

    # ── EMA/RSI/MACD — 모멘텀 컨플루언스(진입)와 하락위험 신호(청산)가 공유. 200선 계산이
    # 안 될 만큼 데이터가 짧아도(신규 상장 등) EMA60 워밍업만 되면 계산한다.
    ema_short = ema_mid = ema_long = None
    rsi_now = macd_hist_now = None
    if idx_now >= EMA_LONG:
        ema_short = _ema(closes, EMA_SHORT)
        ema_mid = _ema(closes, EMA_MID)
        ema_long = _ema(closes, EMA_LONG)
        rsi_series = _rsi(closes, RSI_PERIOD)
        rsi_now = float(rsi_series.iloc[idx_now])
        macd_hist_now = float(_macd_histogram(closes, MACD_FAST, MACD_SLOW, MACD_SIGNAL).iloc[idx_now])
        result['rsi'] = round(rsi_now, 2)
        result['macd_hist'] = round(macd_hist_now, 6)

        # ── 최근 COIN_RSI_BREAKOUT_LOOKBACK개 확정 캔들 중 RSI가 COIN_RSI_BREAKOUT_THRESHOLD를
        # 넘은 적이 있는지(지금은 식었어도 최근에 과열됐던 종목을 잡아내는 용도)
        lookback = Config.COIN_RSI_BREAKOUT_LOOKBACK
        window_start = max(0, idx_now - lookback + 1)
        recent_rsi = rsi_series.iloc[window_start: idx_now + 1]
        result['rsi_recent_breakout'] = bool((recent_rsi >= Config.COIN_RSI_BREAKOUT_THRESHOLD).any())
        result['rsi_recent_breakout_max'] = round(float(recent_rsi.max()), 2)
        result['macd_neg'] = bool(macd_hist_now < 0)
        result['rsi_overbought'] = bool(rsi_now > RSI_OVERBOUGHT)
        # 데드크로스: EMA5가 EMA20을 이번 캔들에 하향 돌파했거나, EMA20이 이미 EMA60 아래(역배열)
        result['ema_dead_cross'] = bool(
            (ema_short.iloc[idx_now] < ema_mid.iloc[idx_now]
             and ema_short.iloc[idx_now - 1] >= ema_mid.iloc[idx_now - 1])
            or ema_mid.iloc[idx_now] < ema_long.iloc[idx_now]
        )

    # ── 200이동평균선 근접 여부
    if idx_now + 1 >= MA200_PERIOD:
        ma200 = closes.iloc[idx_now - MA200_PERIOD + 1: idx_now + 1].mean()
        dist_pct = (close_now - ma200) / ma200 * 100
        result['ma200'] = round(float(ma200), 6)
        result['ma200_dist_pct'] = round(float(dist_pct), 2)
        result['near_ma200'] = bool(abs(dist_pct) <= Config.COIN_MA200_NEAR_PCT)
        result['below_ma200'] = bool(dist_pct < -Config.COIN_MA200_NEAR_PCT)

        # ── 모멘텀 컨플루언스: 200선 위 + EMA 5/20/60 골든크로스(이번 캔들에 막 교차) + RSI<70 +
        # MACD 히스토그램 양수. idx_now+1>=200이 이미 보장돼 있어 EMA60/RSI14/MACD(12,26,9) 워밍업은
        # 항상 충분하다(별도 데이터량 체크 불필요).
        golden_cross = bool(
            ema_short.iloc[idx_now] > ema_mid.iloc[idx_now]
            and ema_short.iloc[idx_now - 1] <= ema_mid.iloc[idx_now - 1]
            and ema_mid.iloc[idx_now] > ema_long.iloc[idx_now]
        )
        rsi_ok = bool(rsi_now < RSI_OVERBOUGHT)
        macd_ok = bool(macd_hist_now > 0)
        result['momentum_confluence'] = bool(close_now > ma200) and golden_cross and rsi_ok and macd_ok

    # ── 일목균형표 구름 위 여부 (26봉 전에 계산된 선행스팬을 지금 캔들과 비교)
    cloud = _calc_cloud(df)
    result['above_cloud'] = cloud['above_cloud']
    result['below_cloud'] = cloud['below_cloud']

    # ── 4시간봉 돌파 (거래량 급증 + 상승 + "처음") 여부
    breakout = _calc_breakout(df, VOL_RATIO_THRESHOLD, Config.COIN_BREAKOUT_RATE_THRESHOLD, Config.COIN_BREAKOUT_VOL_LOOKBACK)
    result['breakout_4h'] = breakout['breakout']
    result['breakout_vol_ratio'] = breakout['vol_ratio']
    result['breakout_candle_rate'] = breakout['candle_rate']

    return result


def run_coin_screening(trigger_type: str = 'auto'):
    """전체 KRW 마켓 코인의 4시간봉 데이터로 매매 후보 필터 지표를 계산해 DB에 저장한다
    (돌파/구름위는 일봉도 병행 계산). 실행 시각/결과는 job_run_log에도 남겨서 "언제 다시
    수집됐는지" 이력을 동기화 관리 페이지에서 확인할 수 있게 한다."""
    logger.info("코인 스크리닝 시작 (Upbit, 4시간봉 + 돌파/구름위 일봉)")
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

            # 돌파(breakout_4h)/구름위(above_cloud)와 같은 로직을 일봉으로도 병행 계산 — 4시간봉과
            # 별개 API 호출이라 실패해도(신규 상장/데이터 부족 등) 전체 스크리닝은 계속 진행하고
            # breakout_1d/above_cloud_1d만 False로 둔다.
            try:
                df_1d = pyupbit.get_ohlcv(ticker, interval=INTERVAL_1D, count=CANDLE_COUNT_1D)
                time.sleep(0.15)
                if df_1d is not None and len(df_1d) >= 8:
                    breakout_1d = _calc_breakout(
                        df_1d, VOL_RATIO_THRESHOLD_1D, Config.COIN_BREAKOUT_RATE_THRESHOLD_1D,
                        Config.COIN_BREAKOUT_VOL_LOOKBACK_1D,
                    )
                    indicators['breakout_1d'] = breakout_1d['breakout']
                    indicators['breakout_1d_vol_ratio'] = breakout_1d['vol_ratio']
                    indicators['breakout_1d_candle_rate'] = breakout_1d['candle_rate']
                    indicators['above_cloud_1d'] = _calc_cloud(df_1d)['above_cloud']
            except Exception as e:
                logger.error(f"[{ticker}] 일봉 돌파/구름 지표 계산 실패: {e}")

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
