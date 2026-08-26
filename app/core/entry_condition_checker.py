"""정밀 매수조건(일봉/5분봉/1분봉) 검사 루프 — 대시보드에서 "정밀검사" 체크한 종목만 대상으로,
자동매매 루프(TRADE_LOOP_INTERVAL_SEC)와 별개 주기(TRADE_CONDITION_CHECK_INTERVAL_SEC, 기본 60초)로
캔들을 조회해 app/core/entry_conditions.py의 판단 결과를 trade_condition_status에 캐시한다.

python main.py condition_check 로 독립 프로세스 실행(auto_trader.py의 trade와 같은 이유로 격리 —
main.py의 start_all()에는 포함하지 않음). auto_trader.py의 evaluate_entries()는 이 캐시만 읽고
직접 캔들을 재조회하지 않는다(전체 후보를 매 매매 사이클마다 다중 시간대로 조회하면 API 호출이
너무 많아지므로, 사용자가 지정한 소수 종목만 이 별도 루프가 담당).
"""
import time

import pyupbit

from app.utils.logger import get_logger
from app.core.entry_conditions import evaluate_conditions
from app.utils.db_manager import (
    get_condition_watch_tickers,
    get_trade_condition_settings,
    save_condition_status,
    save_job_run_log,
    get_trade_strategy_settings,
)

logger = get_logger()

JOB_NAME = "entry_condition_check"
BROKER, MODE = "upbit", "paper"


def _get_candles(ticker: str, interval: str, count: int):
    """pyupbit 공개 API로 캔들 조회(인증 불필요) — 실패 시 None."""
    try:
        return pyupbit.get_ohlcv(ticker, interval=interval, count=count)
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
        condition_settings = get_trade_condition_settings()
        for ticker in tickers:
            try:
                result = evaluate_conditions(ticker, condition_settings, _get_candles)
                save_condition_status(BROKER, MODE, ticker, result['passed'], result['detail'])
                checked += 1
            except Exception as e:
                logger.error(f"[{ticker}] 정밀조건 검사 실패: {e}")
            time.sleep(0.15)  # 코인 스크리닝과 동일하게 공개 API 남용 방지용 딜레이
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
                    description='정밀 매수조건(일봉/5분봉/1분봉) 검사 사이클',
                    api_used='pyupbit(public)',
                    start_time=start_time, end_time=end_time,
                    success=success, count=checked, error_message=error_message,
                    trigger_type=trigger_type,
                )
            except Exception as e:
                logger.error(f"job_run_log 기록 실패: {e}")

    return {'checked': checked}


def run_condition_check_loop(interval_sec: int = None):
    fixed_interval = interval_sec
    logger.info("🔎 정밀 매수조건(일봉/5분봉/1분봉) 검사 루프 시작 — '정밀검사' 체크된 종목만 대상.")
    while True:
        current_interval = fixed_interval or get_trade_strategy_settings()['condition_check_interval_sec']
        try:
            run_condition_check_cycle(trigger_type='auto')
        except Exception as e:
            logger.error(f"정밀조건 검사 루프 오류: {e}")
        time.sleep(current_interval)


if __name__ == "__main__":
    run_condition_check_loop()
