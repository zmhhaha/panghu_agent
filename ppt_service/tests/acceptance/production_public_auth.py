#!/usr/bin/env python3
"""Observe the public production edge without any user/session credentials.

Fixed HTTPS origin, no redirects or cookie jar, no application writes. Forged
claims are deliberately unsigned public test strings, never real credentials.
Response bodies and authorization redirect query parameters are not retained.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request


ORIGIN = 'https://ppt-service.panghuer.top'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def unsigned_token():
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    return encode({'alg': 'none', 'typ': 'JWT'}) + '.' + encode({
        'iss': 'https://auth.panghuer.top', 'sub': '27714443',
        'aud': 'ece3f52410b046fe0952', 'exp': int(time.time()) + 60}) + '.'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[3]
    commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    relative = Path(__file__).resolve().relative_to(root).as_posix()
    frozen = subprocess.check_output(['git', '-C', str(root), 'show', commit + ':' + relative])
    if frozen != Path(__file__).read_bytes():
        raise RuntimeError('operator_must_be_committed_git_source')
    forged = {'X-Auth-Request-User': '27714443', 'X-Auth-Request-Email': 'forged@example.invalid',
              'X-Forwarded-User': '27714443', 'X-Forwarded-For': '127.0.0.1',
              'X-Real-IP': '127.0.0.1', 'X-Forwarded-Access-Token': unsigned_token()}
    project = '/api/v1/projects/c9d480b5-be18-46ed-839b-e65a9bd57eda'
    cases = [
        ('anonymous-profile', '/api/v1/me', {}, {401}),
        ('anonymous-projects', '/api/v1/projects', {}, {401}),
        ('anonymous-known-project', project, {}, {401}),
        ('forged-profile', '/api/v1/me', forged, {401}),
        ('forged-known-project', project, forged, {401}),
        ('malformed-bearer', '/api/v1/me', {'Authorization': 'Bearer invalid-public-probe'}, {401}),
        ('unsigned-bearer', '/api/v1/me', {'Authorization': 'Bearer ' + unsigned_token()}, {401}),
        ('forged-and-unsigned', project, forged | {'Authorization': 'Bearer ' + unsigned_token()}, {401}),
        ('callback-without-state', '/oauth2/callback', {}, {400, 403}),
        ('callback-forged-state', '/oauth2/callback?code=public-invalid-code&state=public-invalid-state', {}, {400, 403}),
        # Keep malformed-state failures above separate from a syntactically
        # valid nonce:redirect pair with no legitimate CSRF cookie. The pinned
        # upstream proxy uses different rejection branches for these inputs.
        ('callback-shaped-without-csrf', '/oauth2/callback?code=public-invalid-code&state=public-invalid-state%3A%2F', {}, {403}),
        ('callback-shaped-external-redirect', '/oauth2/callback?code=public-invalid-code&state=public-invalid-state%3Ahttps%3A%2F%2Fexample.invalid%2F', {}, {403}),
        ('callback-unrequested-error', '/oauth2/callback?error=access_denied&state=public-invalid-state', {}, {400, 403}),
        ('anonymous-root-login', '/', {}, {302}),
    ]
    opener = urllib.request.build_opener(NoRedirect())
    rows = []
    for name, path, headers, expected in cases:
        started = time.monotonic()
        row = dict(name=name, path=path.split('?')[0], expected_status=sorted(expected), passed=False)
        try:
            request = urllib.request.Request(ORIGIN + path, headers={'User-Agent': 'PPT-public-auth-acceptance/1', **headers})
            try:
                response = opener.open(request, timeout=12)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                body = response.read(262145)
                location = urllib.parse.urlsplit(response.headers.get('Location', ''))
                row.update(status=response.status, body_bytes=len(body),
                           body_sha256=hashlib.sha256(body).hexdigest(),
                           redirect_origin=(location.scheme + '://' + location.netloc) if location.netloc else None,
                           redirect_path=location.path if location.netloc else None)
                row['passed'] = response.status in expected and len(body) <= 262144
                if name == 'anonymous-root-login':
                    row['passed'] &= location.scheme == 'https' and location.netloc == 'auth.panghuer.top'
        except (OSError, urllib.error.URLError) as error:
            row['error_type'] = type(error).__name__
        row['seconds'] = round(time.monotonic() - started, 3)
        rows.append(row)
    summary = dict(passed=all(row['passed'] for row in rows), cases=rows, source_commit=commit,
                   operator_sha256=hashlib.sha256(frozen).hexdigest(), origin=ORIGIN,
                   user_credentials_used=False, cookie_jar_used=False, redirects_followed=False,
                   application_writes=0, model_requests=0,
                   scope='public edge rejection only; real owner ACL and authenticated lifecycle are separate')
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({'passed': summary['passed'], 'cases': len(rows),
                      'failed': [row['name'] for row in rows if not row['passed']],
                      'output': str(args.output / 'summary.json')}), flush=True)
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
