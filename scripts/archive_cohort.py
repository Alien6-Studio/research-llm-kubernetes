"""Preserve a complete cohort as lossless, individually hashed public files."""
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys

source, destination = map(Path, sys.argv[1:3])
destination.mkdir(parents=True, exist_ok=True)
files = []
for original in sorted(source.iterdir()):
    if not original.is_file():
        continue
    raw = original.read_bytes()
    path = destination / (original.name + '.gz')
    encoded = gzip.compress(raw, compresslevel=9, mtime=0)
    if path.exists() and path.read_bytes() != encoded:
        raise RuntimeError('Refusing to change an existing archive: ' + str(path))
    path.write_bytes(encoded)
    files.append({'path': str(path), 'source_relative_path': str(original),
                  'sha256': hashlib.sha256(encoded).hexdigest(),
                  'decoded_sha256': hashlib.sha256(raw).hexdigest(),
                  'decoded_bytes': len(raw), 'bytes': len(encoded)})
manifest = {'exported_at_utc': datetime.now(timezone.utc).isoformat(),
            'format': 'Lossless gzip copies of every file in the cohort directory; raw observations are unchanged.',
            'files': files}
(destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps({'files': len(files), 'largest_archive_bytes': max(f['bytes'] for f in files)}))
