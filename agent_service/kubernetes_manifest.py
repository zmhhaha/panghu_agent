"""Compare measured capsule Pod security fields, not self-reported annotations.

Kubernetes may add scheduling/default fields and normalize resource quantities.
It may not replace executable code, identity, credentials, mounts or isolation.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

from .contracts import ServiceError


def _require(condition):
    if not condition:
        raise ServiceError('kubernetes_resource_mismatch', 503)


def _quantity(value):
    _require(type(value) is str and len(value) <= 80)
    match = re.fullmatch(r'([+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))([eE][+-]?[0-9]+|[numkKMGTPE]i?)?', value)
    _require(match is not None)
    number, suffix = match.groups()
    suffix = suffix or ''
    factors = {'': 1, 'n': Decimal('0.000000001'), 'u': Decimal('0.000001'), 'm': Decimal('0.001'),
               'k': 1000, 'K': 1000, 'M': 1000**2, 'G': 1000**3, 'T': 1000**4,
               'P': 1000**5, 'E': 1000**6, **{x + 'i': 1024**n for n, x in enumerate('KMGTPE', 1)}}
    try:
        if suffix.startswith(('e', 'E')) and len(suffix) > 1:
            exponent = int(suffix[1:])
            _require(abs(exponent) <= 30)
            factor = Decimal(10) ** exponent
        else:
            _require(suffix in factors)
            factor = Decimal(factors[suffix])
        value = Decimal(number) * factor
        _require(value.is_finite() and value >= 0)
        return value
    except (InvalidOperation, ValueError, OverflowError):
        raise ServiceError('kubernetes_resource_mismatch', 503) from None


def _same(actual, expected):
    _require(type(actual) is type(expected) and actual == expected)


def _named(values):
    _require(type(values) is list and all(type(x) is dict and type(x.get('name')) is str for x in values))
    result = {x['name']: x for x in values}
    _require(len(result) == len(values))
    return result


def _environment(actual, expected):
    rows, wanted = _named(actual), _named(expected)
    _require(rows.keys() == wanted.keys())
    for name, value in rows.items():
        row, approved = dict(value), dict(wanted[name])
        source = row.get('valueFrom')
        if type(source) is dict and type(source.get('fieldRef')) is dict:
            source = {**source, 'fieldRef': dict(source['fieldRef'])}
            if 'apiVersion' not in approved.get('valueFrom', {}).get('fieldRef', {}):
                _require(source['fieldRef'].pop('apiVersion', 'v1') == 'v1')
            row['valueFrom'] = source
        _same(row, approved)


def _security(actual, expected, *, container):
    _require(type(actual) is dict and type(expected) is dict)
    safe = {'privileged': False, 'runAsNonRoot': False, 'procMount': 'Default',
            'allowPrivilegeEscalation': False} if container else {'runAsNonRoot': False}
    for field, value in expected.items():
        if field == 'capabilities':
            caps = actual.get(field, {})
            _require(type(caps) is dict and set(caps) <= {'add', 'drop'})
            for key in ('add', 'drop'):
                rows, approved = caps.get(key, []), value.get(key, [])
                _require(type(rows) is list and all(type(x) is str for x in rows)
                         and len(set(rows)) == len(rows) and set(rows) == set(approved))
        else:
            _same(actual.get(field), value)
    for field in set(actual) - set(expected):
        if field in safe:
            _same(actual[field], safe[field])
        elif field in {'seccompProfile', 'appArmorProfile'}:
            _same(actual[field], {'type': 'RuntimeDefault'})
        elif field in {'supplementalGroups', 'sysctls'}:
            _same(actual[field], [])
        elif field in {'seLinuxOptions', 'windowsOptions'}:
            _same(actual[field], {})
        elif field == 'supplementalGroupsPolicy':
            _require(actual[field] in {'Merge', 'Strict'})
        else:
            _require(False)


def _resources(actual, expected):
    _require(type(actual) is dict and set(actual) <= {'limits', 'requests', 'claims'})
    _require(not actual.get('claims'))
    for field in ('limits', 'requests'):
        rows, approved = actual.get(field, {}), expected.get(field, {})
        _require(type(rows) is dict)
        for name, value in approved.items():
            _require(name in rows and _quantity(rows[name]) == _quantity(value))
        for name in set(rows) - set(approved):
            # The API defaults a missing request to its explicit limit.
            _require(field == 'requests' and name in expected.get('limits', {})
                     and _quantity(rows[name]) == _quantity(expected['limits'][name]))


def _volumes(actual, expected):
    rows, wanted = _named(actual), _named(expected)
    _require(rows.keys() == wanted.keys())
    for name, row in rows.items():
        approved = wanted[name]
        _require(set(row) == set(approved))
        kinds = set(row) - {'name'}
        _require(len(kinds) == 1)
        kind = next(iter(kinds))
        source, fixed = row[kind], approved[kind]
        _require(type(source) is dict)
        if kind == 'emptyDir':
            _require(set(source) <= {'medium', 'sizeLimit'})
            _same(source.get('medium', ''), fixed.get('medium', ''))
            _require('sizeLimit' in source and _quantity(source['sizeLimit']) == _quantity(fixed['sizeLimit']))
        elif kind in {'secret', 'configMap'}:
            changed = dict(source)
            if 'optional' not in fixed:
                _same(changed.pop('optional', False), False)
            _same(changed, fixed)
        else:
            # hostPath, projected service-account tokens, devices and arbitrary
            # CSI/NFS/PVC mounts do not belong to this capsule's fixed plan.
            _require(False)


def _mounts(actual, expected):
    rows, wanted = _named(actual), _named(expected)
    _require(rows.keys() == wanted.keys())
    for name, row in rows.items():
        changed, approved = dict(row), dict(wanted[name])
        if 'readOnly' not in approved:
            _same(changed.pop('readOnly', False), False)
        for field in ('subPath', 'subPathExpr'):
            if field not in approved:
                _same(changed.pop(field, ''), '')
        if 'mountPropagation' not in approved:
            _same(changed.pop('mountPropagation', 'None'), 'None')
        if 'recursiveReadOnly' not in approved:
            _same(changed.pop('recursiveReadOnly', 'Disabled'), 'Disabled')
        _same(changed, approved)


def _ports(actual, expected):
    rows, wanted = _named(actual), _named(expected)
    _require(rows.keys() == wanted.keys())
    for name, row in rows.items():
        changed, approved = dict(row), dict(wanted[name])
        if 'protocol' not in approved:
            _same(changed.pop('protocol', 'TCP'), 'TCP')
        if 'hostPort' not in approved:
            _same(changed.pop('hostPort', 0), 0)
        if 'hostIP' not in approved:
            _same(changed.pop('hostIP', ''), '')
        _same(changed, approved)


def _container(actual, expected):
    _require(type(actual) is dict)
    critical = {'name', 'image', 'imagePullPolicy', 'command', 'args'}
    for key in critical:
        _same(actual.get(key), expected.get(key))
    _environment(actual.get('env', []), expected.get('env', []))
    _security(actual.get('securityContext', {}), expected.get('securityContext', {}), container=True)
    _resources(actual.get('resources', {}), expected.get('resources', {}))
    _mounts(actual.get('volumeMounts', []), expected.get('volumeMounts', []))
    _ports(actual.get('ports', []), expected.get('ports', []))
    special = {'env', 'securityContext', 'resources', 'volumeMounts', 'ports'}
    benign = {'terminationMessagePath': '/dev/termination-log', 'terminationMessagePolicy': 'File',
              'stdin': False, 'stdinOnce': False, 'tty': False, 'workingDir': ''}
    empty = {'envFrom', 'volumeDevices', 'resizePolicy'}
    for key in set(actual) - critical - special:
        if key in expected:
            _same(actual[key], expected[key])
        elif key in benign:
            _same(actual[key], benign[key])
        elif key in empty:
            _same(actual[key], [])
        else:
            # In particular lifecycle/exec probes, restart rules, extra device
            # claims and alternate working directories cannot run trusted code.
            _require(False)


def validate_pod(actual, expected):
    """Raise a fixed ServiceError when the observed execution plan changed."""
    try:
        _require(type(actual) is dict and type(expected) is dict)
        _require(actual.get('kind', 'Pod') == 'Pod' and actual.get('apiVersion', 'v1') == 'v1')
        spec, fixed = actual['spec'], expected['spec']
        _require(type(spec) is dict and type(fixed) is dict)
        for key in ('automountServiceAccountToken', 'restartPolicy', 'activeDeadlineSeconds',
                    'terminationGracePeriodSeconds'):
            _same(spec.get(key), fixed[key])
        for key in ('hostPID', 'hostIPC', 'hostNetwork', 'shareProcessNamespace'):
            _same(spec.get(key, False), fixed.get(key, False))
        _require(not spec.get('initContainers') and not spec.get('ephemeralContainers'))
        _require(type(spec.get('containers')) is list and len(spec['containers']) == 1)
        _container(spec['containers'][0], fixed['containers'][0])
        _security(spec.get('securityContext', {}), fixed.get('securityContext', {}), container=False)
        _volumes(spec.get('volumes', []), fixed.get('volumes', []))
        handled = {'containers', 'securityContext', 'volumes', 'initContainers', 'ephemeralContainers',
                   'automountServiceAccountToken', 'restartPolicy', 'activeDeadlineSeconds',
                   'terminationGracePeriodSeconds', 'hostPID', 'hostIPC', 'hostNetwork', 'shareProcessNamespace'}
        scheduling = {'nodeName', 'nodeSelector', 'affinity', 'tolerations', 'topologySpreadConstraints',
                      'priorityClassName', 'priority', 'preemptionPolicy', 'imagePullSecrets',
                      'schedulingGates', 'overhead'}
        defaults = {'dnsPolicy': 'ClusterFirst', 'schedulerName': 'default-scheduler',
                    'serviceAccountName': 'default', 'serviceAccount': 'default', 'enableServiceLinks': True,
                    'hostUsers': True}
        for key in set(spec) - handled:
            if key in fixed:
                _same(spec[key], fixed[key])
            elif key in scheduling:
                continue
            elif key in defaults:
                _same(spec[key], defaults[key])
            elif key == 'os':
                _same(spec[key], {'name': 'linux'})
            else:
                _require(False)
    except ServiceError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation):
        raise ServiceError('kubernetes_resource_mismatch', 503) from None
