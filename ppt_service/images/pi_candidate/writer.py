"""Detached test writer, killed by the runtime UID cleanup."""
import time
from pathlib import Path
while True:
    with Path('heartbeat').open('a') as handle:
        handle.write('x')
    time.sleep(0.02)
