#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
from config.settings import settings

if __name__ == "__main__":
    uvicorn.run("src.dashboard.app:app", host=settings.dashboard_host, port=settings.dashboard_port)
