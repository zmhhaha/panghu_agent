"""Trusted whole-film limits; per-scene clocks remain independently bounded."""

FPS = 24
LEGACY_MAX_FRAMES = 60 * FPS
MAX_SECONDS = 300
MAX_FRAMES = MAX_SECONDS * FPS
MAX_TIMEOUT_SECONDS = 10800
MAX_FRAME_BYTES = 12 * 1024 * 1024
MAX_STREAM_FRAME_BYTES = 16 * 1024 * 1024 * 1024
STREAM_MODE = 'streaming-png-v2'


def render_timeout(frame_count):
    """Old frozen short runs retain their original execution budget."""
    return 600 if frame_count <= LEGACY_MAX_FRAMES else MAX_TIMEOUT_SECONDS
