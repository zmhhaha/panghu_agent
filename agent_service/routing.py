"""Select a fixed Worker pool by frozen binding, including stopped old routes."""
from .contracts import ServiceError, digest


class RoutedWorkerAdapter:
    def __init__(self, routes):
        self.routes = {}
        modes = set()
        for policy, adapter in routes:
            key = digest(policy.pins())
            if key in self.routes:
                raise ValueError('Duplicate Worker binding route')
            self.routes[key] = (policy, adapter)
            modes.add(getattr(adapter, 'provisioning', 'external_static'))
        if len(modes) != 1:
            raise ValueError('One engine cannot mix provisioning semantics')
        self.provisioning = modes.pop()

    def _adapter(self, envelope):
        for policy, adapter in self.routes.values():
            if policy.matches(envelope):
                return adapter
        raise ServiceError('original_worker_binding_unavailable', 503)

    def ready(self, policy=None):
        if policy is None:
            return any(p.enabled and a.ready(p) for p, a in self.routes.values())
        entry = self.routes.get(digest(policy.pins()))
        return bool(entry and entry[0].enabled and entry[1].ready(policy))

    def receipt_key_for(self, envelope):
        adapter = self._adapter(envelope)
        derive = getattr(adapter, 'receipt_key_for', None)
        return derive(envelope) if callable(derive) else adapter.receipt_key

    def start(self, envelope):
        return self._adapter(envelope).start(envelope)

    def inspect(self, envelope):
        return self._adapter(envelope).inspect(envelope)

    def renew(self, envelope, expires_at):
        return self._adapter(envelope).renew(envelope, expires_at)

    def cancel(self, envelope):
        return self._adapter(envelope).cancel(envelope)

    def events(self, envelope, after=0, limit=100):
        return self._adapter(envelope).events(envelope, after=after, limit=limit)

    def result(self, envelope):
        return self._adapter(envelope).result(envelope)

    def resolve_uncreated(self, envelope, code='worker_not_created'):
        method = getattr(self._adapter(envelope), 'resolve_uncreated', None)
        return method(envelope, code) if callable(method) else None

    def reconcile_quarantined(self, envelope):
        method = getattr(self._adapter(envelope), 'reconcile_quarantined', None)
        return method(envelope) if callable(method) else None

    def close(self):
        for _, adapter in self.routes.values():
            adapter.close()
