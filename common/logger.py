import logging
import sys
from datetime import datetime
from pathlib import Path

def setup_logger(name: str = "tw_quant", log_dir: str = "logs",
                 level: int = logging.INFO) -> logging.Logger:
    """設定專案全域 Logger (P3-3)"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
        
    logger.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    )
    
    # 控制台輸出
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    logger.addHandler(console)
    
    # 檔案輸出
    if log_dir:
        path = Path(log_dir)
        path.mkdir(parents=True, exist_ok=True)
        filename = f"{name}_{datetime.now():%Y%m%d}.log"
        fh = logging.FileHandler(path / filename, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        
    return logger

# 建立預設 logger
logger = setup_logger()
