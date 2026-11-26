# Shared authentication source retained for reproducible builds

This first-party module is retained from the already serving video-workbench
image, rather than introducing a new authentication scheme during the spatial
release. It provides the existing proxy identity, scoped service credentials,
CSRF and write authorization used by that application.

- Serving image: `arm-cluster-master:5000/video-workbench:latest@sha256:d7db8e589071b99cdf5278b72731e961468e8283f87c681825964f42d1a9b492`.
- Original image OCI source label: `f16f8c9ccc116a4b8bcd31dd205aa8a1e7e9e03c`; the corresponding original Git
  object was unavailable when this source was recovered. That label is provenance,
  not a claim that this repository contains the original object.
- Original path: `/app/service_auth/__init__.py`.
- Exact recovered bytes: 11,877.
- SHA-256: `122dc6134abb9d1d25ac209cb4226b41782035cdebbb1bb820fa193ddd8dc1f4`.

The module imports only the Python standard library and FastAPI, already an
application dependency. The ordinary Workbench Dockerfile now copies this
committed source explicitly. Credentials remain runtime Secret mounts; no
credential values, image layers or runtime monkey patches are stored here.
