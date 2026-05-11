import logging
from logging.handlers import RotatingFileHandler
from maa_api.config.config import LOG_PATH
from datetime import datetime

log_dir = LOG_PATH
date_str = datetime.now().strftime("%Y-%m-%d")

logger = logging.getLogger("maa_api")
logger.setLevel(logging.DEBUG)

formatter = logging.Formatter(
    fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(formatter)

info_file_handler = RotatingFileHandler(log_dir / f"{date_str}.log", maxBytes=10**6, backupCount=20, encoding="utf-8")
info_file_handler.setLevel(logging.INFO)
info_file_handler.setFormatter(formatter)

error_file_handler = RotatingFileHandler(log_dir / f"{date_str}.error.log", maxBytes=10**6, backupCount=20, encoding="utf-8")
error_file_handler.setLevel(logging.ERROR)
error_file_handler.setFormatter(formatter)

logger.addHandler(console_handler)
logger.addHandler(info_file_handler)
logger.addHandler(error_file_handler)

uvicorn_access_logger = logging.getLogger("uvicorn.access")
uvicorn_access_logger.addHandler(info_file_handler)
uvicorn_access_logger.addHandler(console_handler)

uvicorn_error_logger = logging.getLogger("uvicorn.error")
uvicorn_error_logger.addHandler(error_file_handler)
uvicorn_error_logger.addHandler(console_handler)
