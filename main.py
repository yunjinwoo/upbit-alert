import argparse
import sys
import multiprocessing
from app.api.server import run_server
from app.core.upbit_monitor import run_upbit_monitor
from app.core.stock_monitor import run_stock_monitor
from app.core.upbit_market_analysis import run_coin_screening_loop
from app.core.auto_trader import run_auto_trade_loop
from app.core.entry_condition_checker import run_condition_check_loop
from app.utils.logger import get_logger

logger = get_logger()

def start_all():
    logger.info("Starting all services...")

    # Use multiprocessing to run services in parallel
    p_api = multiprocessing.Process(target=run_server, kwargs={'use_reloader': False})
    p_upbit = multiprocessing.Process(target=run_upbit_monitor)
    p_stock = multiprocessing.Process(target=run_stock_monitor)
    p_coin_analysis = multiprocessing.Process(target=run_coin_screening_loop)

    p_api.start()
    p_upbit.start()
    p_stock.start()
    p_coin_analysis.start()

    p_api.join()
    p_upbit.join()
    p_stock.join()
    p_coin_analysis.join()

if __name__ == "__main__":
    multiprocessing.freeze_support()
    
    parser = argparse.ArgumentParser(description="Upbit & Stock Alert System")
    parser.add_argument("mode", nargs="?",
                        choices=["all", "api", "upbit", "stock", "coin_analysis", "trade", "condition_check"],
                        default="all",
                        help="Mode to run: all (default), api, upbit, stock, coin_analysis, trade, or condition_check")
    # trade(자동매매 1단계: 업비트 모의매매)/condition_check(정밀 매수조건 검사)는 의도적으로
    # all/start_all()에 포함하지 않음 — 알림/모니터링 프로세스와 장애를 격리하기 위해
    # `python main.py trade` / `python main.py condition_check`로 각각 독립 실행할 것.

    args = parser.parse_args()

    if args.mode == "all":
        start_all()
    elif args.mode == "api":
        run_server(use_reloader=True)
    elif args.mode == "upbit":
        run_upbit_monitor()
    elif args.mode == "stock":
        run_stock_monitor()
    elif args.mode == "coin_analysis":
        run_coin_screening_loop()
    elif args.mode == "trade":
        run_auto_trade_loop()
    elif args.mode == "condition_check":
        run_condition_check_loop()
