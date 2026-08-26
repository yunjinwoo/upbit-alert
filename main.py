import argparse
import sys
import multiprocessing
from app.api.server import run_server
from app.core.upbit_monitor import run_upbit_monitor
from app.core.stock_monitor import run_stock_monitor
from app.core.upbit_market_analysis import run_coin_screening_loop
from app.core.auto_trader import run_auto_trade_loop, run_live_trade_loop
from app.core.entry_condition_checker import run_condition_check_loop
from app.core.toss_market_analysis import run_stock_screening_loop
from app.core.toss_auto_trader import (
    run_auto_trade_loop as run_toss_trade_loop,
    run_live_trade_loop as run_toss_live_trade_loop,
)
from app.core.toss_entry_condition_checker import run_condition_check_loop as run_toss_condition_check_loop
from app.core.brokers.upbit_live_broker import print_selected_balance
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
                        choices=["all", "api", "upbit", "stock", "coin_analysis", "trade", "condition_check",
                                 "toss_analysis", "toss_trade", "toss_condition_check", "live_balance",
                                 "live_trade", "toss_live_trade"],
                        default="all",
                        help="Mode to run: all (default), api, upbit, stock, coin_analysis, trade, condition_check, "
                             "toss_analysis, toss_trade, toss_condition_check, live_balance, live_trade, or toss_live_trade")
    # trade/condition_check(업비트)와 toss_trade/toss_analysis/toss_condition_check(토스)는 의도적으로
    # all/start_all()에 포함하지 않음 — 알림/모니터링 프로세스와 장애를 격리하기 위해 각각
    # `python main.py trade` 등으로 독립 실행할 것.
    # live_balance(업비트 실계좌 잔고 조회, 주문 없음)도 마찬가지로 독립 실행 전용 — start_all()에 없음.
    # live_trade(업비트 실거래 자동매매, 실주문 발생 가능)/toss_live_trade(토스증권 실거래 자동매매,
    # 실주문 발생 가능)는 절대 all/start_all()에 포함하지 않고, 각각의 모의매매 프로세스와도 완전히
    # 별도 프로세스로만 실행 — 켜져 있어도 대시보드의 "실거래 실행" 스위치(기본 꺼짐)와 실거래 승인
    # 체크박스가 둘 다 있어야 실제 주문이 나간다.

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
    elif args.mode == "toss_analysis":
        run_stock_screening_loop()
    elif args.mode == "toss_trade":
        run_toss_trade_loop()
    elif args.mode == "toss_condition_check":
        run_toss_condition_check_loop()
    elif args.mode == "live_balance":
        try:
            print_selected_balance()
        except RuntimeError as e:
            logger.error(str(e))
            sys.exit(1)
    elif args.mode == "live_trade":
        run_live_trade_loop()
    elif args.mode == "toss_live_trade":
        run_toss_live_trade_loop()
