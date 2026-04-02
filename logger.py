import logging
import sys
from logging.handlers import RotatingFileHandler

# Define log format
log_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# Create a logger
logger = logging.getLogger('upbit_alert')
logger.setLevel(logging.INFO)

# Console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_format)
logger.addHandler(console_handler)

# File handler (Rotating to keep log size manageable)
file_handler = RotatingFileHandler('app.log', maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
file_handler.setFormatter(log_format)
logger.addHandler(file_handler)

def get_logger():
    return logger
