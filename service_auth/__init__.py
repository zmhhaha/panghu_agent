"""Shared proxy identities, Vault-backed service credentials and write protection."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit

from fastapi import HTTPException


CSRF_COOKIE = '__Host-service_csrf'
LOCAL_CSRF_COOKIE = 'service_csrf'
TOKEN_FILE_ENV = 'SERVICE_AUTH_TOKENS_FILE'
ISSUER_ENV = 'SERVICE_AUTH_ISSUER'
MAX_TOKEN_CONFIG_BYTES = 1024 * 1024
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')
SUBJECT = re.compile(r'service:[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}\Z')
CSRF_TOKEN = re.compile(r'[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]\Z')
SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})
OPERATIONS = frozenset({'read', 'write'})
AUDIT = logging.getLogger('uvicorn.error').getChild('service_auth')


@dataclass(frozen=True)
class Principal:
    subject: str
    issuer: str
    auth_kind: str
    credential_id: str | None = None
    username: str = ''
    display_name: str = ''
    operations: frozenset[str] = frozenset()


def _reject(status, code):
    raise HTTPException(status, detail={'code': code})


def _text(value, limit):
    return (isinstance(value, str) and 0 < len(value) <= limit
            and all(ord(character) >= 32 and ord(character) != 127 for character in value))


def _identifier(value):
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def _unique_keys(pairs):
    values = dict(pairs)
    if len(values) != len(pairs):
        raise ValueError()
    return values


def _configuration(token_file):
    """Read each request so a projected Secret update takes effect without restart."""
    try:
        if not token_file:
            raise ValueError()
        with Path(token_file).open('rb') as source:
            raw = source.read(MAX_TOKEN_CONFIG_BYTES + 1)
        if not raw or len(raw) > MAX_TOKEN_CONFIG_BYTES:
            raise ValueError()
        document = json.loads(raw, object_pairs_hook=_unique_keys)
        if (type(document) is not dict or set(document) != {'version', 'principals', 'credentials'}
                or type(document['version']) is not int or document['version'] != 1):
            raise ValueError()
        principals, credentials = document['principals'], document['credentials']
        if (type(principals) is not dict or len(principals) > 128
                or type(credentials) is not list or len(credentials) > 512):
            raise ValueError()
        subjects = set()
        for key, identity in principals.items():
            if (not _identifier(key) or type(identity) is not dict
                    or not {'subject'} <= identity.keys()
                    or identity.keys() - {'subject', 'username', 'display_name', 'enabled'}
                    or type(identity.get('enabled', True)) is not bool
                    or not isinstance(identity['subject'], str)
                    or SUBJECT.fullmatch(identity['subject']) is None
                    or identity['subject'] in subjects):
                raise ValueError()
            for field in ('username', 'display_name'):
                if field in identity and not _text(identity[field], 128):
                    raise ValueError()
            subjects.add(identity['subject'])
        hashes, identifiers = set(), set()
        for credential in credentials:
            if (type(credential) is not dict
                    or set(credential) != {'id', 'principal', 'token_hash', 'expires_at', 'services', 'operations'}
                    or not _identifier(credential['id']) or credential['id'] in identifiers
                    or not isinstance(credential['principal'], str) or credential['principal'] not in principals
                    or not isinstance(credential['token_hash'], str)
                    or re.fullmatch(r'[0-9a-f]{64}', credential['token_hash']) is None
                    or credential['token_hash'] in hashes
                    or not isinstance(credential['expires_at'], str)
                    or type(credential['services']) is not list or not credential['services']
                    or any(not _identifier(value) for value in credential['services'])
                    or len(set(credential['services'])) != len(credential['services'])):
                raise ValueError()
            expiry = datetime.fromisoformat(credential['expires_at'].replace('Z', '+00:00'))
            if expiry.tzinfo is None or expiry.utcoffset() is None:
                raise ValueError()
            operations = credential['operations']
            if (type(operations) is not list or not operations
                    or any(not isinstance(value, str) or value not in OPERATIONS for value in operations)
                    or len(set(operations)) != len(operations)):
                raise ValueError()
            identifiers.add(credential['id'])
            hashes.add(credential['token_hash'])
        return principals, credentials
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        _reject(503, 'service_auth_configuration_invalid')


def _service_principal(authorization, *, service, issuer, token_file, method):
    scheme, separator, token = authorization.partition(' ')
    token = token.strip()
    if (not separator or scheme.lower() != 'bearer' or not 32 <= len(token) <= 512
            or any(not 33 <= ord(character) <= 126 for character in token)):
        _reject(401, 'service_auth_token_invalid')
    principals, credentials = _configuration(token_file)
    supplied_hash = hashlib.sha256(token.encode()).hexdigest()
    for credential in credentials:
        if not hmac.compare_digest(supplied_hash, credential['token_hash']):
            continue
        expiry = datetime.fromisoformat(credential['expires_at'].replace('Z', '+00:00'))
        if expiry <= datetime.now(timezone.utc):
            _reject(401, 'service_auth_token_expired')
        if service not in credential['services']:
            _reject(403, 'service_auth_service_forbidden')
        operation = 'read' if method in SAFE_METHODS else 'write'
        if operation not in credential['operations']:
            _reject(403, 'service_auth_operation_forbidden')
        identity = principals[credential['principal']]
        if not identity.get('enabled', True):
            _reject(403, 'service_auth_principal_disabled')
        return Principal(identity['subject'], issuer, 'service', credential['id'],
                         identity.get('username', ''), identity.get('display_name', ''),
                         frozenset(credential['operations']))
    _reject(401, 'service_auth_token_invalid')


def _trusted_peer(request, trusted_proxy_cidrs):
    if trusted_proxy_cidrs is None:
        return
    try:
        peer = ipaddress.ip_address(request.client.host)
        networks = [ipaddress.ip_network(value) for value in trusted_proxy_cidrs]
    except (AttributeError, TypeError, ValueError):
        _reject(503, 'service_auth_proxy_configuration_invalid')
    if not networks or not any(peer in network for network in networks):
        _reject(401, 'authentication_required')


def authenticate(request, *, service, issuer=None, token_file=None,
                 trusted_proxy_cidrs=None, local_subject=None) -> Principal:
    """Resolve one authenticated identity; invalid machine credentials never fall back."""
    cached = getattr(request.state, 'service_principal', None)
    if cached is not None:
        if not isinstance(cached, Principal) or getattr(request.state, 'service_auth_service', None) != service:
            _reject(503, 'service_auth_request_state_invalid')
        return cached
    if not _identifier(service):
        _reject(503, 'service_auth_configuration_invalid')
    if local_subject is not None:
        if (not _text(local_subject, 200) or request.client is None
                or request.client.host not in {'127.0.0.1', '::1', 'testclient'}):
            _reject(401, 'authentication_required')
        principal = Principal(local_subject, 'local', 'local')
    else:
        issuer = os.getenv(ISSUER_ENV, '') if issuer is None else issuer
        if not _text(issuer, 512):
            _reject(503, 'service_auth_issuer_missing')
        authorizations = request.headers.getlist('authorization')
        if authorizations:
            if len(authorizations) != 1:
                _reject(401, 'service_auth_token_invalid')
            if not request.url.path.startswith('/api/'):
                _reject(403, 'service_auth_route_forbidden')
            token_file = os.getenv(TOKEN_FILE_ENV, '') if token_file is None else token_file
            principal = _service_principal(authorizations[0], service=service, issuer=issuer,
                                           token_file=token_file, method=request.method)
            AUDIT.info('service_access service=%s actor=%s credential=%s operation=%s', service,
                       principal.subject, principal.credential_id,
                       'read' if request.method in SAFE_METHODS else 'write')
        else:
            _trusted_peer(request, trusted_proxy_cidrs)
            subject = request.headers.get('x-forwarded-user', '')
            if (not _text(subject, 200) or any(character.isspace() for character in subject)
                    or subject.startswith('service:')):
                _reject(401, 'authentication_required')
            principal = Principal(subject, issuer, 'browser')
    request.state.service_principal = principal
    request.state.service_auth_service = service
    return principal


def _csrf_binding(principal):
    return hashlib.sha256(f'{principal.issuer}\0{principal.subject}'.encode()).hexdigest()


def issue_csrf(request, response, *, local_mode=False) -> str:
    principal = getattr(request.state, 'service_principal', None)
    if not isinstance(principal, Principal):
        _reject(401, 'authentication_required')
    if principal.auth_kind == 'service':
        return ''
    name = LOCAL_CSRF_COOKIE if local_mode else CSRF_COOKIE
    binding = _csrf_binding(principal)
    cookie_binding, separator, token = request.cookies.get(name, '').partition('.')
    if not separator or cookie_binding != binding or CSRF_TOKEN.fullmatch(token) is None:
        token = secrets.token_urlsafe(32)
    response.set_cookie(name, f'{binding}.{token}', secure=not local_mode, httponly=True,
                        samesite='strict', path='/')
    return token


def require_write(request, principal, *, allowed_origins=None, local_mode=False):
    if request.method in SAFE_METHODS:
        return
    if getattr(request.state, 'service_principal', None) is not principal:
        _reject(401, 'authentication_required')
    if principal.auth_kind == 'service':
        return
    origin = request.headers.get('origin', '')
    if allowed_origins is None:
        try:
            parsed = urlsplit(origin)
        except ValueError:
            _reject(403, 'origin_rejected')
        permitted = (parsed.scheme in {'http', 'https'} and parsed.netloc == request.headers.get('host', '')
                     and not parsed.path and not parsed.query and not parsed.fragment)
    else:
        permitted = origin in allowed_origins
    if not permitted:
        _reject(403, 'origin_rejected')
    name = LOCAL_CSRF_COOKIE if local_mode else CSRF_COOKIE
    binding, separator, cookie = request.cookies.get(name, '').partition('.')
    supplied = request.headers.get('x-csrf-token', '')
    if (not separator or binding != _csrf_binding(principal)
            or CSRF_TOKEN.fullmatch(cookie) is None or CSRF_TOKEN.fullmatch(supplied) is None
            or not hmac.compare_digest(cookie, supplied)):
        _reject(403, 'csrf_rejected')
