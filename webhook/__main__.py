"""
python -m webhook

Start the webhook server.
Defaults to 127.0.0.1:8000 locally; deployment platforms can set HOST/PORT.
Expose to TradingView with:  ngrok http 8000
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "webhook.app:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
