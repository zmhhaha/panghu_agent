"""Explicit owner grant for a new clock, carrying the same unused allocation.

This contract is available only through the separate window endpoint. It never
changes the original grant, authorization, deadline, limits or actual expense.
"""
from copy import deepcopy
import math

from .scene_source_import_resume import REQUEST_FIELDS,validate_request,require,_certificate_common

SCHEMA='video.scene-source-import-resume-window.v1'
EXTRA_FIELDS={'renewal_authorization_sha256','phase_execution_deadline_at','ack_unused_allocation_only'}


def validate_window_request(value,now,*,validate_clock=True):
    require(type(value) is dict and set(value)==REQUEST_FIELDS|EXTRA_FIELDS,
        'invalid_scene_source_import_resume_window_request',422)
    base=validate_request({key:value[key] for key in REQUEST_FIELDS})
    import re
    require(type(value['renewal_authorization_sha256']) is str
        and re.fullmatch('[a-f0-9]{64}',value['renewal_authorization_sha256'])
        and value['renewal_authorization_sha256']!=base['authorization_sha256']
        and value['ack_unused_allocation_only'] is True
        and type(value['phase_execution_deadline_at']) in (int,float)
        and math.isfinite(value['phase_execution_deadline_at'])
        and value['phase_execution_deadline_at']>0
        and (not validate_clock or now<value['phase_execution_deadline_at']<=now+3600),
        'scene_source_import_resume_window_not_authorized',409)
    return deepcopy(value)


def validate_window_certificate(value,grant):
    value=_certificate_common(value,grant,schema=SCHEMA,extra_fields=EXTRA_FIELDS)
    require(value['ack_unused_allocation_only'] is True
        and value['renewal_authorization_sha256']!=grant['authorization_sha256']
        and type(value['phase_execution_deadline_at']) in (int,float)
        and math.isfinite(value['phase_execution_deadline_at'])
        and value['issued_at']<value['phase_execution_deadline_at']<=value['issued_at']+3600,
        'scene_source_import_resume_window_identity_changed')
    return value
