#!/usr/bin/env python3
"""Compare independent labels and saved review predictions, without any model call."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps' / 'api'))
from app.services.judge_calibration import measure_calibration


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--labels', required=True, type=Path)
    parser.add_argument('--predictions', required=True, type=Path)
    parser.add_argument('--split', choices=['calibration', 'held_out'], default='held_out')
    parser.add_argument('--require-human', action='store_true')
    args = parser.parse_args()
    try:
        metrics = measure_calibration(json.loads(args.labels.read_text()), json.loads(args.predictions.read_text()),
                                      split=args.split, require_human=args.require_human)
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f'Calibration input error: {exc}\n')
    print(json.dumps(metrics, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
