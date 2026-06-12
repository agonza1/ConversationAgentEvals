from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / 'apps' / 'api'))

from app.services.voice_lab import build_default_voice_lab_runner, seeded_voice_lab_scenarios


def main() -> int:
    runner = build_default_voice_lab_runner(PROJECT_ROOT)
    report = runner.run(seeded_voice_lab_scenarios())

    artifact_dir = PROJECT_ROOT / 'artifacts' / 'voice-lab'
    artifact_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    output_path = artifact_dir / f'voice-lab-proof-{timestamp}.json'
    latest_path = artifact_dir / 'voice-lab-proof-latest.json'
    payload = json.dumps(report, indent=2)
    output_path.write_text(payload)
    latest_path.write_text(payload)

    print(f'Saved voice lab proof artifact: {output_path}')
    print(json.dumps(report['summary'], indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
