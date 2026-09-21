import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor

tracks = monitor.apple()
print("APPLE_COUNT", len(tracks))
print(json.dumps(tracks[:20], ensure_ascii=False, indent=2))

if len(tracks) < 100:
    raise SystemExit(1)
if len(tracks[:20]) != 20:
    raise SystemExit(1)
