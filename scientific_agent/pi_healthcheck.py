"""Authenticated local probe; no token appears in argv or probe output."""
import argparse
import json
import os
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ready', action='store_true')
    args = parser.parse_args()
    token = os.environ.get('SCIENTIFIC_PI_WORKER_TOKEN', '')
    if not token:
        return 1
    try:
        request = Request('http://127.0.0.1:8080/v1/health', headers={'Authorization': 'Bearer ' + token})
        with urlopen(request, timeout=3) as response:
            data = json.load(response)
        healthy = data.get('worker_ready') if args.ready else data.get('alive')
        return 0 if healthy is True else 1
    except Exception:
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
