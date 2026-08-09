import pyupbit
import time
import requests
from datetime import datetime, timedelta
from app.config import Config
from app.utils.logger import get_logger
from app.utils.google_sheets import init_sheet, save_to_sheet, get_daily_volume_info
from app.utils.db_manager import get_today_alert_count

logger = get_logger()

def send_slack_msg(text):
    if not Config.SLACK_WEBHOOK_URL:
        logger.warning("Slack webhook URL not configured.")
        return
    try:
        payload = {"text": text}
        requests.post(Config.SLACK_WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        logger.error(f"슬랙 전송 실패: {e}")

def get_volume_ratio(ticker, interval, lookback=Config.UPBIT_VOL_AVG_LOOKBACK):
    """현재(진행 중) 캔들 거래량이 직전 lookback개 확정 캔들의 평균 거래량 대비 몇 배인지 계산한다.
    신규 상장 등으로 lookback만큼 확정 캔들이 쌓이지 않아 평균을 낼 수 없으면 None(데이터 없음)을 반환한다.
    (예: 상장 1~2주된 코인은 주봉이 1~2개뿐이라 20주 평균을 못 냄 — 이 타임프레임은 감시 대상에서 제외됨)
    """
    try:
        df = pyupbit.get_ohlcv(ticker, interval=interval, count=lookback + 1)
        if df is None or len(df) < 2:
            return None

        vol_list = df['volume'].tolist()
        curr_vol = float(vol_list[-1])
        avg_vol = sum(vol_list[:-1]) / len(vol_list[:-1])

        if avg_vol == 0:
            return None

        ratio = curr_vol / avg_vol
        logger.info(f" {ticker} - {interval} = {ratio:.2f} (최근 {len(vol_list) - 1}봉 평균 대비)")
        return ratio
    except Exception as e:
        logger.error(f"[{ticker}-{interval}] 에러: {e}")
        return None

def run_upbit_monitor():
    logger.info(f"🚀 전 타임프레임 동시 폭발 감시 시작! (Upbit)")
    init_sheet()
    
    target_tickers = pyupbit.get_tickers(fiat="KRW")
    intervals = Config.UPBIT_INTERVALS
    thresholds = Config.UPBIT_THRESHOLDS
    
    last_notified_time = {ticker: {intv: None for intv in intervals} for ticker in target_tickers}
    skip_cache = {}
    skip_duration_alert = timedelta(seconds=Config.UPBIT_SKIP_DURATION_ALERT)

    while True:
        active_count = len(target_tickers) - len(skip_cache)
        logger.info(f"\n{'=' * 40}")
        logger.info(f"감시 대상: {active_count}/{len(target_tickers)}개 (스킵: {len(skip_cache)}개)")
        logger.info(f"{'=' * 40}")

        for i, ticker in enumerate(target_tickers, 1):
            if ticker in skip_cache and datetime.now() < skip_cache[ticker]:
                continue

            # ① 일봉 먼저 체크
            daily = get_daily_volume_info(ticker)
            if daily is None:
                time.sleep(2.0)
                continue

            skip_cache.pop(ticker, None)

            # ④ 분봉 조회 — 데이터가 부족한(None) 타임프레임은 아예 감시 대상에서 제외
            ratios = {}
            for interval in intervals.keys():
                r = get_volume_ratio(ticker, interval)
                if r is not None:
                    ratios[interval] = r
                time.sleep(0.4)

            total_intervals = len(ratios)  # 이 티커에서 실제로 평가 가능했던 타임프레임 수
            surge_count = 0
            active_intervals = []

            for interval, ratio in ratios.items():
                name = intervals[interval]
                threshold = thresholds.get(interval, 3.0)
                if ratio >= threshold:
                    surge_count += 1
                    active_intervals.append(f"{name}(*{ratio:.1f}배* / 기준:{threshold}배)")

            daily_str = (
                f"▲{daily['increase_rate']}%  "
                f"거래량: {daily['today_vol']:,}  "
                f"거래대금: {daily['trade_value']:,}원"
            )
            
            if surge_count >= 2:
                logger.info(f" surge_count: {surge_count}, active_intervals: {active_intervals}")

                if ratios.get("minutes240", 0) >= 1:
                    df_now = pyupbit.get_ohlcv(ticker, interval="minutes5", count=1)
                    if df_now is not None and not df_now.empty:
                        current_candle_time = df_now.index[0]

                        if last_notified_time.get(ticker, {}).get("alert") != current_candle_time:
                            save_to_sheet(ticker, active_intervals, surge_count, daily_str, total_intervals)

                            # 코인 알림은 Slack 발송 없이 DB/시트 저장만 수행 (Slack은 주식 Signal Score A등급 전용)
                            today_count = get_today_alert_count(ticker)
                            logger.info(f"[{time.strftime('%H:%M:%S')}] {ticker} {today_count}회째 수집 - DB/시트 저장 완료 (Slack 알림 없음)")

                            skip_cache[ticker] = datetime.now() + skip_duration_alert
                            
                            if ticker not in last_notified_time:
                                last_notified_time[ticker] = {}
                            last_notified_time[ticker]["alert"] = current_candle_time
            
            time.sleep(2.0)

        logger.info(f"{'=' * 40} == END 1시간 후 다시 동작 ")
        time.sleep(60 * 60)

if __name__ == "__main__":
    run_upbit_monitor()
