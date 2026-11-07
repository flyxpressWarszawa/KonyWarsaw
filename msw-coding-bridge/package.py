#!/usr/bin/env python3
"""Build and read back a deterministic source-only WordPress ZIP; never install."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

SLUG = 'msw-github-coding-bridge'
SOURCE = Path(__file__).resolve().parent / 'wordpress' / SLUG

def build(output):
    header = (SOURCE / (SLUG + '.php')).read_text(encoding='utf-8')
    match = re.search(r'^\s*\*?\s*Version:\s*([0-9]+\.[0-9]+\.[0-9]+)\s*$', header, re.M)
    if not match: raise ValueError('Missing plugin version')
    entries = sorted(SOURCE.rglob('*'))
    if any(p.is_symlink() for p in entries): raise ValueError('Symlink in package')
    files = [p for p in entries if p.is_file()]
    if not files or len(files) > 100 or any(p.suffix not in {'.php','.md','.txt'} for p in files):
        raise ValueError('Unexpected package files')
    data = [(SLUG + '/' + p.relative_to(SOURCE).as_posix(), p.read_bytes()) for p in files]
    if sum(len(raw) for _, raw in data) > 1048576: raise ValueError('Package source too large')
    output.mkdir(parents=True, exist_ok=True)
    archive = output / (SLUG + '-' + match[1] + '.zip')
    with zipfile.ZipFile(archive, 'w') as z:
        for name, raw in data:
            item = zipfile.ZipInfo(name, date_time=(2026,1,1,0,0,0))
            item.create_system = 3
            item.external_attr = 0o100644 << 16
            z.writestr(item, raw, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    with zipfile.ZipFile(archive) as z:
        if z.testzip() or z.namelist() != [name for name,_ in data] or any(z.read(name) != raw for name,raw in data):
            raise ValueError('Archive readback failed')
    raw = archive.read_bytes()
    result = {'schema':'msw.coding-package.v1','status':'prepared_not_installed','slug':SLUG,
              'version':match[1],'archive':archive.name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
              'production_changed':False,'source_files':[{'path':name,'sha256':hashlib.sha256(body).hexdigest()} for name,body in data]}
    (output/'package-manifest.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    (output/'SHA256SUMS').write_text(result['sha256']+'  '+archive.name+'\n',encoding='utf-8')
    return result

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,required=True)
    print(json.dumps(build(p.parse_args().output_dir),sort_keys=True))
