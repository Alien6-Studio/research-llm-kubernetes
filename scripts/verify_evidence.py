"""Verify published SHA-256 inventories without rerunning an experiment."""
from pathlib import Path
import hashlib
import sys

ROOT = Path(__file__).resolve().parents[1]
checked = 0
for inventory in sorted((ROOT / "evidence").glob("checksums-*.sha256")):
    for line in inventory.read_text().splitlines():
        if not line.strip():
            continue
        expected, relative = line.split(maxsplit=1)
        path = (ROOT / relative.lstrip("*")).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            sys.exit(f"Invalid evidence path in {inventory.name}: {relative}")
        observed = hashlib.sha256(path.read_bytes()).hexdigest()
        if observed != expected:
            sys.exit(f"Evidence checksum mismatch: {relative}")
        checked += 1
print(f"Verified {checked} published evidence files. This checks identity, not scientific validity.")
