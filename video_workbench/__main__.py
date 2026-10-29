"""Run one workbench coordinator process; no local Agent runtime is started."""
import argparse
import os

import uvicorn

from .app import create_app


def main():
    parser = argparse.ArgumentParser(description='SVG / HTML 视频工作台')
    parser.add_argument('--local', action='store_true', help='仅回环地址，使用本地开发身份')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8089)
    args = parser.parse_args()
    if args.local and args.host not in {'127.0.0.1', '::1', 'localhost'}:
        parser.error('--local 只能绑定回环地址')
    hosts = os.environ.get('VIDEO_WORKBENCH_HOSTS', 'localhost,127.0.0.1,[::1]').split(',')
    app = create_app(local_mode=args.local, trusted_hosts={h.strip() for h in hosts if h.strip()})
    uvicorn.run(app, host=args.host, port=args.port, workers=1, proxy_headers=False)


if __name__ == '__main__':
    main()
