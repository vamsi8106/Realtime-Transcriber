import logging
import sys

def setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    fmt = "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
    handler.setFormatter(logging.Formatter(fmt))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [handler]
