"""Terminal entry point; bare invocation produces the challenge CSV."""
import argparse
import sys
from pathlib import Path

from app import App, Chat
from domain import DataError
from history import export_all


def main(argv=None):
    parser=argparse.ArgumentParser(description='Buy or Wait? Verified affordability with saved chat.')
    parser.add_argument('mode',nargs='?',default='batch',choices=['batch','chat','samples','audit','export','preflight'])
    parser.add_argument('--dataset',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--user-id')
    parser.add_argument('--session-id')
    parser.add_argument('--offline',action='store_true',help='Use validated cached model results only')
    parser.add_argument('--diagnose',action='store_true',help='Continue after evidence errors; never write an incomplete submission')
    args=parser.parse_args(argv)
    root=Path(__file__).resolve().parent.parent
    try:
        if args.mode=='export':
            print(export_all(root)); return 0
        app=App(root,args.dataset,args.offline,args.mode)
        if args.mode=='preflight':
            app.gemini.preflight()
            print(f'Gemini preflight passed for {app.gemini.model}: image input and structured output.')
            return 0
        if args.mode=='audit':
            for name,rows in app.dataset.tables.items(): print(f'{name}: {len(rows)}')
            print('Dataset schema, joins, image presence and payment totals validated.')
            return 0
        if args.mode=='chat':
            if not args.user_id: parser.error('chat requires --user-id')
            Chat(app,args.user_id,args.session_id).run()
            return 0
        return 0 if app.batch(samples=args.mode=='samples',diagnose=args.diagnose,output=args.output) else 2
    except (DataError,OSError,ValueError) as exc:
        print(f'Error: {exc}',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
