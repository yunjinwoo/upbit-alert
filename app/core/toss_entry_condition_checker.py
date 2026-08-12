"""정밀 매수조건(일봉/5분봉/1분봉) 검사 루프 — 토스증권(국내주식)판.
app/core/entry_condition_checker.py(업비트)와 완전히 동일한 구조이며, 캔들 조회만
app/core/toss_client.get_candles_resampled로 바꾸고 broker/mode를 'toss'/'paper'로 고정한다.

python main.py toss_condition_check 로 독립 프로세스 실행 (main.py의 start_all()에는 포함하지
않음 — 업비트 쪽과 동일한 이유로 프로세스 격리).
"""
import time

from app.utils.logger import get_logger
from app.core.entry_conditions import evaluate_conditions
from app.core.toss_client import get_candles_resampled
from app.utils.db_manager import (
    get_condition_watch_tickers,
    get_trade_condition_settings,
    save_condition_status,
    save_job_run_log,
    get_trade_strategy_settings,
)

logger = get_logger()

JOB_NAME = "entry_condition_check_toss"
BROKER, MODE = "toss", "paper"


def _get_candles(ticker: str, interval: str, count: int):
    """토스 Open API로 캔들 조회 — 실패 시 None. interval은 evaluate_conditions가 넘기는
    'day'/'minute5'/'minute1' 그대로 get_candles_resampled에 전달한다(5분봉은 1분봉을 리샘플링)."""
    try:
        return get_candles_resampled(ticker, interval, count)
    except Exception as e:
        logger.error(f"[{ticker}] {interval} 캔들 조회 실패: {e}")
        return None


def run_condition_check_cycle(trigger_type: str = None) -> dict:
    """1사이클: "정밀검사" 체크된 종목마다 켜진 조건들을 계산해 trade_condition_status에 저장."""
    start_time = time.strftime('%Y-%m-%d %H:%M:%S')
    success = True
    error_message = None
    checked = 0
    try:
        tickers = get_condition_watch_tickers(BROKER, MODE)
        condition_settings = get_trade_condition_settings(BROKER)
        for ticker in tickers:
            try:
                result = evaluate_conditions(ticker, condition_settings, _get_candles)
                save_condition_status(BROKER, MODE, ticker, result['passed'], result['detail'])
                checked += 1
            except Exception as e:
                logger.error(f"[{ticker}] 정밀조건 검사 실패: {e}")
            time.sleep(0.1)  # 토스 API rate limit 대비 여유(스크리닝과 동일한 이유)
    except Exception as e:
        success = False
        error_message = str(e)
        raise
    finally:
        if trigger_type is not None:
            end_time = time.strftime('%Y-%m-%d %H:%M:%S')
            try:
                save_job_run_log(
                    job_name=JOB_NAME,
                    description='정밀 매수조건(일봉/5분봉/1분봉) 검사 사이클(토스)',
                    api_used='toss openapi',
                    start_time=start_time, end_time=end_time,
                    success=success, count=checked, error_message=error_message,
                    trigger_type=trigger_type,
                )
            except Exception as e:
                logger.error(f"job_run_log 기록 실패: {e}")

    return {'checked': checked}


def run_condition_check_loop(interval_sec: int = None):
    fixed_interval = interval_sec
    logger.info("🔎 정밀 매수조건(일봉/5분봉/1분봉) 검사 루프 시작(토스) — '정밀검사' 체크된 종목만 대상.")
    while True:
        current_interval = fixed_interval or get_trade_strategy_settings(BROKER)['condition_check_interval_sec']
        try:
            run_condition_check_cycle(trigger_type='auto')
        except Exception as e:
            logger.error(f"정밀조건 검사 루프 오류(토스): {e}")
        time.sleep(current_interval)


if __name__ == "__main__":
    run_condition_check_loop()
