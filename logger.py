import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from config import _current_path, config

__all__ = [
  'logger'
]

__LOG_DIR = f'{_current_path}/logs/'
__LOG_NAME = 'keyword_alert.log'
if config['logger']['path']:
    __LOG_DIR = config['logger']['path'].rstrip('/')

if not os.path.exists(__LOG_DIR):
    os.makedirs(__LOG_DIR)
__LOG_FILE = f"{__LOG_DIR}/{__LOG_NAME}"

# Fix level setting: properly get logging level constant, default to ERROR
level_name = config['logger']['level'].upper()
__level = logging.DEBUG

# Create formatter
formatter = logging.Formatter(fmt='[%(levelname)s][%(name)s][%(asctime)s]-->%(message)s',datefmt='%Y-%m-%d %H:%M:%S%Z')

# File handler (optional backup)
file_handler = RotatingFileHandler(__LOG_FILE, maxBytes=5*1024*1024, backupCount=10) # 最大50MB日志
file_handler.setFormatter(formatter)

# Console handler - all logs go to stdout
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
console_handler.setLevel(__level)  # Ensure console handler respects the log level

# Configure logger
logger = logging.getLogger('keyword_alert.root')
logger.setLevel(__level)
# Add stdout handler first (primary output)
logger.addHandler(console_handler)
# Add file handler as backup
logger.addHandler(file_handler)
# Prevent duplicate logs from propagating to root logger
logger.propagate = False

# Also configure root logger to ensure all logs go to stdout
root_logger = logging.getLogger()
root_logger.setLevel(__level)
# Remove any existing handlers from root logger to avoid duplicates
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)