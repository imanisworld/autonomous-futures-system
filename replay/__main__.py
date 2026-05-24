from __future__ import annotations

import argparse
import json

from replay.replay_engine import ReplayEngine


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline candle replay")
    parser.add_argument("--candles", nargs="+", help="Path(s) to replay JSONL candles")
    parser.add_argument("--manifest", help="Path to replay manifest JSON")
    parser.add_argument("--log-dir", default="logs/replay", help="Replay log directory")
    parser.add_argument("--date", help="Replay date YYYY-MM-DD")
    args = parser.parse_args()

    if args.manifest and args.candles:
        parser.error("use either --manifest or --candles, not both")
    if not args.manifest and not args.candles:
        parser.error("one of --manifest or --candles is required")

    engine = ReplayEngine(log_dir=args.log_dir)
    if args.manifest:
        report = engine.run_manifest(args.manifest)
    elif len(args.candles) == 1:
        report = engine.run(args.candles[0], review_date=args.date)
    else:
        report = engine.run_many(args.candles)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
