"""Environment backend lifecycle: one server per arm, on its own port.

World state is global to a server and the benchmark always names its
character `Hero`, so two concurrent runs sharing one backend corrupt each
other. Each arm therefore gets its own backend, and each backend needs its
own store:

  SQLite  `app/db.py` pins the *relative* path `artifact.db`, and the
          routers open *relative* data paths like `app/Data/items.json`.
          So cwd must contain both. We give each arm a directory holding an
          `app` symlink into the repo; the db then lands in the arm dir.

  Redis   `app/db.py` pins `redis://localhost:6379` (db 0) and calls
          `flushdb()` at startup. Arms are separated onto distinct db
          indices by the sitecustomize shim.

Neither backend file is modified.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import registry
from paths import REPO_ROOT, SHIM_DIR, venv_bin

BACKENDS = {
    "sqlite": REPO_ROOT / "Virtual_Environment" / "FastApi_SQLite_Ver",
    "redis": REPO_ROOT / "Virtual_Environment" / "FastApi_Redis_Ver",
}


def server_record_path(port: int) -> Path:
    return registry.servers_dir() / f"{port}.json"


def health(port: int, timeout: float = 1.5) -> bool:
    """Alive if it answers HTTP at all. /maps/0/0 exists on both backends."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/maps/0/0", timeout=timeout
        ) as response:
            return response.status == 200
    except urllib.error.HTTPError:
        return True  # answered, so the process is serving
    except Exception:
        return False


def wait_healthy(port: int, timeout: float = 120.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if health(port):
            return True
        time.sleep(0.5)
    return False


def prepare_arm_dir(port: int, backend: str) -> Path:
    """Per-arm cwd with the `app` symlink the relative paths need."""
    directory = registry.arm_dir(port)
    link = directory / "app"
    target = BACKENDS[backend] / "app"
    if link.is_symlink() or link.exists():
        if link.is_symlink() and Path(os.readlink(link)) == target:
            return directory
        link.unlink()
    link.symlink_to(target)
    return directory


def start(port: int, backend: str = "sqlite", redis_db: int | None = None) -> dict:
    """Start one backend. Idempotent: a healthy server on the port is reused."""
    if backend not in BACKENDS:
        raise ValueError(f"unknown backend {backend!r}; expected one of {list(BACKENDS)}")

    existing = registry.read_record(server_record_path(port))
    if existing and registry.pid_alive(existing.get("pid")) and health(port):
        return existing

    directory = prepare_arm_dir(port, backend)
    log_path = registry.logs_dir() / f"server-{port}.log"

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SHIM_DIR)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    if backend == "redis":
        env["HEROBENCH_REDIS_DB"] = str(redis_db if redis_db is not None else port - 8000)

    log_file = open(log_path, "ab", buffering=0)
    process = subprocess.Popen(
        # **Bound to loopback, because `fastapi run` defaults to 0.0.0.0.**
        # The backend authenticates nothing and its endpoints create, mutate
        # and delete characters, so a default bind put an unauthenticated
        # writer for every in-flight run on the whole LAN. Anything that talks
        # to it is on this host by construction: the agents hardcode
        # 127.0.0.1 in A1_Agent/env_api/api.py and the health and log probes
        # in this file use it too, so nothing loses reach.
        [
            str(venv_bin("fastapi")),
            "run",
            str(BACKENDS[backend] / "main.py"),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(directory),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    record = {
        "kind": "env_server",
        "port": port,
        "backend": backend,
        "redis_db": env.get("HEROBENCH_REDIS_DB"),
        "pid": process.pid,
        "cwd": str(directory),
        "log_file": str(log_path),
        "started_at": time.time(),
        "state": "running",
    }
    registry.write_record(server_record_path(port), record)

    if not wait_healthy(port):
        record["state"] = "failed"
        record["error"] = f"did not become healthy; see {log_path}"
        registry.write_record(server_record_path(port), record)
    return record


def stop(port: int) -> bool:
    path = server_record_path(port)
    record = registry.read_record(path)
    if not record:
        return False
    stopped = registry.stop_process(record.get("pid"))
    record["state"] = "stopped"
    record["ended_at"] = time.time()
    registry.write_record(path, record)
    return stopped


def character_log(port: int, amount: int = 12, name: str = "Hero") -> list[dict]:
    """Recent actions, for the live view. Server-side fetch avoids CORS."""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/logs/{amount}/{name}", timeout=2.0
        ) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("data") or data.get("logs") or []
    return data if isinstance(data, list) else []
