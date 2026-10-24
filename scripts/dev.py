"""Run API + Dash locally and stop both on Ctrl-C."""

import os
import signal
import socket
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")
api_port = os.getenv("API_PORT", "8000")
dash_port = os.getenv("DASH_PORT", "8050")
# Dash must talk to the API on the port actually started here.
os.environ.setdefault("API_URL", f"http://127.0.0.1:{api_port}")


def busy(port: str) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", int(port))) == 0


for name, port in (("API_PORT", api_port), ("DASH_PORT", dash_port)):
    if busy(port):
        sys.exit(f"Порт {port} занят. Укажите другой: {name}=<порт> uv run python scripts/dev.py")

commands = [
    [
        sys.executable,
        "-m",
        "uvicorn",
        "apps.api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        api_port,
        "--no-access-log",
    ],
    [sys.executable, "-m", "apps.dashboard.app"],
]
processes = [subprocess.Popen(command) for command in commands]
print(
    f"Интерфейс: http://127.0.0.1:{dash_port} · API: {os.environ['API_URL']}/docs · Ctrl-C — стоп",
    flush=True,
)


def stop(*_):
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        process.wait(timeout=10)


signal.signal(signal.SIGTERM, stop)
try:
    for process in processes:
        if process.wait() != 0:
            stop()
            sys.exit(1)
except KeyboardInterrupt:
    stop()
