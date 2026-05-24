from __future__ import annotations

import argparse
import json

from replay.replay_engine import ReplayEngine


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline candle replay")
    parser.add_argument("--candles", nargs="+", required=True, help="Path(s) to replay JSONL candles")
    parser.add_argument("--log-dir", default="logs/replay", help="Replay log directory")
    parser.add_argument("--date", help="Replay date YYYY-MM-DD")
    args = parser.parse_args()

    engine = ReplayEngine(log_dir=args.log_dir)
    if len(args.candles) == 1:
        report = engine.run(args.candles[0], review_date=args.date)
    else:
        report = engine.run_many(args.candles)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
