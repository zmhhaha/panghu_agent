"""Fake Pi executable: no model, network or credentials needed."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

mode = sys.argv[1]
if "--version" in sys.argv:
    if mode == "version_large":
        print("x" * 10000, file=sys.stderr, flush=True)
        time.sleep(90)
    if mode == "version_log":
        print("unexpected log", file=sys.stderr)
    print("24.21.0" if mode == "node" else ("99.0.0" if mode == "wrong_version" else "0.73.1"),
          file=sys.stderr if mode == "stderr_version" else sys.stdout)
    raise SystemExit(0)


def emit(value):
    print(json.dumps(value), flush=True)


Path("observed.json").write_text(json.dumps({"env": dict(os.environ), "cwd": os.getcwd(), "argv": sys.argv}), encoding="utf-8")
if mode in ("wait", "descendant"):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    Path("child.pid").write_text(str(child.pid))
if mode == "wait":
    emit({"type": "agent_start"})
    time.sleep(90)
if mode == "idle":
    time.sleep(90)
if mode == "bad_json":
    print("unexpected interactive banner", flush=True)
    time.sleep(90)
if mode == "large":
    print("x" * 20000, flush=True)
    time.sleep(90)
if mode == "exit":
    print("PRIVATE_CONTROL_TOKEN appears in stderr", file=sys.stderr, flush=True)
    raise SystemExit(7)
provider = sys.argv[sys.argv.index("--provider") + 1]
model = sys.argv[sys.argv.index("--model") + 1]
emit({"type": "session", "version": 3, "cwd": "SECRET_PATH"})
emit({"type": "agent_start"})
emit({"type": "tool_execution_start", "args": {"secret": "PRIVATE_CONTROL_TOKEN"}, "toolName": "bash"})
emit({"type": "tool_execution_end", "result": {"output": "PRIVATE_CONTROL_TOKEN"}})
message = {"role": "assistant", "provider": provider, "model": "wrong-model" if mode == "wrong_model" else model,
           "stopReason": "error" if mode == "model_error" else "stop", "content": []}
emit({"type": "message_end", "message": message})
if mode != "incomplete":
    emit({"type": "agent_end", "messages": [message]})
Path("report.md").write_text("# Fixture report\n", encoding="utf-8")
