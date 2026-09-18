#!/usr/bin/env python3
"""Independent post-16:00 ET safety fallback for the wide-stop Tradovate DEMO lane."""

from __future__ import annotations

import json
import logging
import sys

from config.settings import load_config
from context.wide_stop_demo_runtime import run_demo_eod_fallback

logger = logging.getLogger("wide_stop_demo_eod_fallback")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = load_config()
    result = run_demo_eod_fallback(cfg=cfg, log_dir=cfg.log_dir)
    logger.info("wide-stop DEMO EOD fallback result: %s", json.dumps(result, sort_keys=True, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
