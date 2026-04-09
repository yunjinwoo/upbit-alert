import argparse
import sys
import multiprocessing
from app.api.server import run_server
from app.core.upbit_monitor import run_upbit_monitor
from app.core.stock_monitor import run_stock_monitor
from app.utils.logger import get_logger

logger = get_logger()

def start_all():
    logger.info("Starting all services...")
    
    # Use multiprocessing to run services in parallel
    p_api = multiprocessing.Process(target=run_server)
    p_upbit = multiprocessing.Process(target=run_upbit_monitor)
    p_stock = multiprocessing.Process(target=run_stock_monitor)
    
    p_api.start()
    p_upbit.start()
    p_stock.start()
    
    p_api.join()
    p_upbit.join()
    p_stock.join()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Upbit & Stock Alert System")
    parser.add_argument("mode", nargs="?", choices=["all", "api", "upbit", "stock"], default="all",
                        help="Mode to run: all (default), api, upbit, or stock")
    
    args = parser.parse_args()
    
    if args.mode == "all":
        start_all()
    elif args.mode == "api":
        run_server()
    elif args.mode == "upbit":
        run_upbit_monitor()
    elif args.mode == "stock":
        run_stock_monitor()
