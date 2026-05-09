"""
Application entrypoint — run with:
    python main.py
    OR
    uvicorn main:app --reload
"""

import uvicorn

from app.main import create_app
from app.core.settings import get_settings

settings = get_settings()
app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_config=None,  # Let structlog handle logging
    )
