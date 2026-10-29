"""Timeouts shared by the buffered task-model proxy and its Pi drivers."""

# HTTPX bounds each upstream I/O wait, not the complete streamed response.
MODEL_PROXY_IO_TIMEOUT_SECONDS = 600.0
MODEL_PROXY_RETURN_GRACE_SECONDS = 30.0


def buffered_model_idle_timeout(max_task_seconds: int) -> float:
    """Allow buffered responses until the independently enforced task deadline.

    A stream can keep the proxy busy without producing native stdout. Short
    tasks retain enough tolerance for one I/O wait and the response handoff;
    long tasks remain governed by the runtime's absolute execution deadline.
    """
    return max(float(max_task_seconds),
               MODEL_PROXY_IO_TIMEOUT_SECONDS + MODEL_PROXY_RETURN_GRACE_SECONDS)
