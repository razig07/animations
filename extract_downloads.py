#!/usr/bin/env python3
"""Extract the delivered badge files. No third-party packages are needed."""
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parent
DEST = ROOT / 'badge_overlays' / 'deliverables'
FOLDERS = {'.mov': 'mov', '.png': 'png', '.svg': 'svg'}

archives = sorted((ROOT / 'downloads').glob('*.zip'))
if not archives:
    raise SystemExit('No badge ZIPs found in downloads/. Download the full repository ZIP first.')
for archive in archives:
    with zipfile.ZipFile(archive) as z:
        for entry in z.infolist():
            name = Path(entry.filename)
            if name.name != entry.filename or name.suffix not in FOLDERS:
                raise SystemExit(f'Unexpected entry in {archive.name}: {entry.filename}')
            folder = DEST / FOLDERS[name.suffix]
            folder.mkdir(parents=True, exist_ok=True)
            with z.open(entry) as src, (folder / name.name).open('wb') as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
    print(f'Extracted {archive.name}')
print(f'Rendered videos: {DEST / "mov"}')
