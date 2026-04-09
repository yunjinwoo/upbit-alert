import logging
import sys
from logging.handlers import RotatingFileHandler
import os

def get_logger(name='upbit_alert'):
    # Define log format
    log_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    # Create a logger
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)

        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(log_format)
        logger.addHandler(console_handler)

        # File handler (Rotating to keep log size manageable)
        # Using a fixed path for log file relative to project root
        log_file = os.path.join(os.getcwd(), 'app.log')
        file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
        file_handler.setFormatter(log_format)
        logger.addHandler(file_handler)

    return logger
