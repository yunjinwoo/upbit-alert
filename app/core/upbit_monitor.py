import pyupbit
import time
import requests
from datetime import datetime, timedelta
from app.config import Config
from app.utils.logger import get_logger
from app.utils.google_sheets import init_sheet, save_to_sheet, get_daily_volume_info

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

def get_volume_ratio(ticker, interval):
    try:
        df = pyupbit.get_ohlcv(ticker, interval=interval, count=2)
        if df is None or len(df) < 2:
            return 0

        vol_list = df['volume'].tolist()
        prev_vol = float(vol_list[0])
        curr_vol = float(vol_list[1])

        if prev_vol == 0:
            return 0

        ratio = curr_vol / prev_vol
        logger.info(f" {ticker} - {interval} = {ratio:.2f}")
        return ratio
    except Exception as e:
        logger.error(f"[{ticker}-{interval}] 에러: {e}")
        return 0

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

            logger.info("-" * 10)
            logger.info(f"{time.strftime('%H:%M:%S')} :: [{i}/{len(target_tickers)}] ticker = {ticker}")

            # ① 일봉 먼저 체크
            daily = get_daily_volume_info(ticker)
            if daily is None:
                logger.info(f"[{ticker}] 일봉 전일 이하 → 스킵")
                time.sleep(2.0)
                continue

            skip_cache.pop(ticker, None)

            # ④ 분봉 조회
            ratios = {}
            for interval in intervals.keys():
                ratios[interval] = get_volume_ratio(ticker, interval)
                time.sleep(0.4)

            surge_count = 0
            active_intervals = []

            for interval, name in intervals.items():
                threshold = thresholds.get(interval, 3.0)
                if ratios[interval] >= threshold:
                    surge_count += 1
                    active_intervals.append(f"{name}(*{ratios[interval]:.1f}배* / 기준:{threshold}배)")

            daily_str = (
                f"▲{daily['increase_rate']}%  "
                f"거래량: {daily['today_vol']:,}  "
                f"거래대금: {daily['trade_value']:,}원"
            )
            
            if surge_count >= 2:
                logger.info(f" surge_count: {surge_count}, active_intervals: {active_intervals}")
                
                if ratios["minutes240"] >= 1:
                    df_now = pyupbit.get_ohlcv(ticker, interval="minutes5", count=1)
                    if df_now is not None and not df_now.empty:
                        current_candle_time = df_now.index[0]
                        
                        if last_notified_time.get(ticker, {}).get("alert") != current_candle_time:
                            surge_text = ", ".join(active_intervals)
                            message = (
                                f"🚨 *[{ticker}] 거래량 조건 만족 ({surge_count}/4)!* 🚨\n"
                                f"폭발한 봉: {surge_text} \n"
                                f" https://upbit.com/exchange?code=CRIX.UPBIT.{ticker}"
                            )

                            save_to_sheet(ticker, active_intervals, surge_count, daily_str)
                            send_slack_msg(message)
                            skip_cache[ticker] = datetime.now() + skip_duration_alert
                            
                            if ticker not in last_notified_time:
                                last_notified_time[ticker] = {}
                            last_notified_time[ticker]["alert"] = current_candle_time
                            logger.info(f"[{time.strftime('%H:%M:%S')}] {ticker} 조건 만족 알림 발송!")
            
            time.sleep(2.0)

        logger.info(f"{'=' * 40} == END 1시간 후 다시 동작 ")
        time.sleep(60 * 60)

if __name__ == "__main__":
    run_upbit_monitor()
