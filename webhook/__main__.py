"""
python -m webhook

Start the webhook server on 127.0.0.1:8000.
Expose to TradingView with:  ngrok http 8000
"""

from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "webhook.app:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
