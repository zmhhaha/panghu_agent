"""Observe only an isolated P1 image's real default entrypoint and loopback."""
import json
import os
import time
import urllib.error
import urllib.request


def main():
    assert os.getuid() == 10000 and os.getgid() == 10000
    assert "PPT_LOCAL_TEST_ONLY" not in os.environ
    assert os.environ["PPT_EXECUTION_ENABLED"] == "false"
    deadline = time.monotonic() + 25
    while True:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/healthz", timeout=2) as response:
                health = json.load(response)
            break
        except (OSError, urllib.error.URLError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)
    assert health == {"status": "ok", "deployment_mode": "test", "manual_acceptance": "pending"}
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/", timeout=2)
        raise AssertionError("unauthorized root was served")
    except urllib.error.HTTPError as response:
        assert response.code == 401
        assert json.load(response) == {"detail": {"code": "authentication_required"}}
    listeners = []
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        with open(path, encoding="ascii") as stream:
            for line in list(stream)[1:]:
                fields = line.split()
                if fields[3] == "0A":
                    listeners.append((path, fields[1]))
    assert listeners == [("/proc/net/tcp", "0100007F:1F40")], listeners
    print(json.dumps({"health": health, "unauthorized_root": 401,
                      "listeners": ["127.0.0.1:8000"], "default_entrypoint": True,
                      "database_connected": False, "network": "none", "model_requests": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
