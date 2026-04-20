"""
Local dev launcher.

    python run.py

Equivalent to:

    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

In production, prefer:

    uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 2
"""

import os
import uvicorn

from app.config import settings


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=bool(int(os.getenv("RELOAD", "1"))),
    )
