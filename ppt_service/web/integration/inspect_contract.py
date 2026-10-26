"""Read-only development project shape probe. Never sends mutations."""
import argparse
import json
import urllib.request
from urllib.parse import quote

parser = argparse.ArgumentParser()
parser.add_argument('--project', required=True, help='Owned, uniquely prefixed integration fixture ID')
args = parser.parse_args()
base = 'http://127.0.0.1:18505'
with urllib.request.urlopen(base + '/api/v1/projects/' + quote(args.project, safe='')) as response:
    current = json.load(response)
print(json.dumps({'keys': sorted(current), 'revision': current['revision'], 'stage': current['stage'], 'asset_count': len(current.get('assets', []))}, ensure_ascii=False))
