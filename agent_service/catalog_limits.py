"""Fixed shared-catalog budgets; historical records are never pruned here."""
from __future__ import annotations

import json

from pydantic import BaseModel

LEGACY_HISTORY_LIMIT = 100
HISTORY_RECORD_LIMIT = 256
CONTRACT_RECORD_LIMIT = 100
CATALOG_KEYS = ('task_contracts', 'capability_bundles', 'tool_environments',
                'execution_bindings', 'routes')
SECTION_LIMITS = {
    'task_contracts': CONTRACT_RECORD_LIMIT, 'routes': CONTRACT_RECORD_LIMIT,
    'capability_bundles': HISTORY_RECORD_LIMIT, 'tool_environments': HISTORY_RECORD_LIMIT,
    'execution_bindings': HISTORY_RECORD_LIMIT, 'kubernetes_workers': HISTORY_RECORD_LIMIT,
}
# Preserve the former reader budget for immutable historical host files. The
# smaller publication budgets below apply to newly prepared ConfigMaps only.
CATALOG_READER_MAX_BYTES = 4 * 1024 * 1024
PUBLISH_CATALOG_MAX_BYTES = 768 * 1024
PUBLISH_PUBLIC_MAX_BYTES = 896 * 1024
CONFIGMAP_MAX_BYTES = 1024 * 1024
PPT_RELEASE_GROWTH = {'execution_bindings': 5, 'kubernetes_workers': 5,
                      'tool_environments': 5, 'capability_bundles': 1}


def wire_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def validate_catalog_budget(catalog):
    if type(catalog) is not dict or set(catalog) != set(CATALOG_KEYS):
        raise ValueError('Invalid complete catalog')
    for key, rows in catalog.items():
        if type(rows) not in (list, tuple) or len(rows) > SECTION_LIMITS[key]:
            raise ValueError('Invalid bounded registry')
    # Registry callers may supply its already validated typed records. Preserve
    # that existing API while counting their ordinary wire representation.
    normalized = {key: [row.model_dump(mode='json') if isinstance(row, BaseModel) else row
                        for row in rows] for key, rows in catalog.items()}
    if len(wire_bytes(normalized)) > CATALOG_READER_MAX_BYTES:
        raise ValueError('Catalog exceeds its reader byte bound')
