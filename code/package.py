"""Create a source-only archive and redacted transcript after validation."""
import argparse
import json
import zipfile
from pathlib import Path

from domain import DataError
from history import export_all


def package(root,allow_incomplete=False):
    root=Path(root)
    report=root/'code'/'evaluation'/'usage_report.json'
    complete=report.exists() and json.loads(report.read_text(encoding='utf-8')).get('complete') and (root/'output.csv').is_file()
    if not complete and not allow_incomplete:
        raise DataError('Final dataset run is incomplete. Use --allow-incomplete only for a development code archive.')
    target=root/'code.zip'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((root/'code').rglob('*')):
            if not path.is_file() or any(p in ('__pycache__','.pytest_cache') for p in path.parts): continue
            if path.suffix not in ('.py','.md','.txt'): continue
            if path.name in ('sample_usage_report.md',): continue
            archive.write(path,path.relative_to(root/'code'))
    export_all(root)
    return target


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--allow-incomplete',action='store_true')
    args=parser.parse_args()
    try: print(package(Path(__file__).resolve().parent.parent,args.allow_incomplete))
    except DataError as exc: parser.exit(2,str(exc)+'\n')
