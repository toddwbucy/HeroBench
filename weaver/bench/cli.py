"""herobench — one entry point for standing up, running, and watching arms.

    herobench up                  bootstrap the venv, start backends, open the view
    herobench run --model ID      launch a benchmark arm
    herobench status              same picture as the dashboard, in text
    herobench stop RUN_ID         stop one run (--all for every run)
    herobench down                stop everything
    herobench adopt --name X      register an agent this harness did not launch
    herobench doctor              check the environment
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import env_server
import progress
import registry
from paths import (
    BENCH_DIR,
    CONSTRAINTS,
    REPO_ROOT,
    SHIM_DIR,
    VENV_DIR,
    venv_bin,
    venv_python,
    venv_ready,
)

DASHBOARD_RECORD = "dashboard.json"
DEFAULT_DASH_PORT = 8090
DEFAULT_BASE_PORT = 8000

SERVICE_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openrouter_openai": "OPENROUTER_API_KEY",
    "ollama": None,
    "hf": None,
}


# ----------------------------------------------------------------- bootstrap


def ensure_venv(with_llm: bool = False, quiet: bool = False) -> None:
    """Create .venv and install what the benchmark needs, honouring constraints."""
    say = (lambda *a: None) if quiet else print
    uv = shutil.which("uv")

    if not venv_ready():
        say(f"creating venv at {VENV_DIR}")
        if uv:
            subprocess.check_call([uv, "venv", "--python", "3.12", str(VENV_DIR)])
        else:
            subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])

    requirements = [REPO_ROOT / "requirements.txt"]
    if with_llm:
        requirements.append(REPO_ROOT / "requirements_llm.txt")

    args: list[str] = []
    for path in requirements:
        args += ["-r", str(path)]
    args += ["openai", "python-dotenv", "-c", str(CONSTRAINTS)]

    say("installing dependencies" + (" (with local-LLM extras)" if with_llm else ""))
    env = os.environ.copy()
    if uv:
        env["VIRTUAL_ENV"] = str(VENV_DIR)
        subprocess.check_call([uv, "pip", "install", *args], env=env)
    else:
        subprocess.check_call([str(venv_bin("pip")), "install", *args], env=env)


def require_venv() -> None:
    if not venv_ready():
        sys.exit("no venv yet — run: herobench up")


# ----------------------------------------------------------------- dashboard


def dashboard_path() -> Path:
    return registry.state_dir() / DASHBOARD_RECORD


def display_host(host: str) -> str:
    """The address to put in a URL, since a bind is not somewhere to click.

    0.0.0.0 means every interface, which is not reachable as written, so a
    printed http://0.0.0.0:8090/ is a dead link on every machine including
    this one. Resolve it to the address this host uses to reach the LAN. The
    UDP connect selects a route and sends no packet, so it works with the
    network down and costs nothing.
    """
    if host not in ("0.0.0.0", "::", ""):
        return host
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.168.0.1", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def dashboard_start(port: int = DEFAULT_DASH_PORT, host: str = "0.0.0.0") -> dict:
    record = registry.read_record(dashboard_path())
    if record and registry.pid_alive(record.get("pid")):
        return record

    log_path = registry.logs_dir() / "dashboard.log"
    log_file = open(log_path, "ab", buffering=0)
    # Stdlib only, so the system python is fine even mid-bootstrap.
    python = str(venv_python()) if venv_ready() else sys.executable
    process = subprocess.Popen(
        [python, str(BENCH_DIR / "dashboard.py"), "--host", host, "--port", str(port)],
        cwd=str(BENCH_DIR),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    record = {
        "kind": "dashboard",
        "pid": process.pid,
        "host": host,
        "port": port,
        "url": f"http://{display_host(host)}:{port}/",
        "log_file": str(log_path),
        "started_at": time.time(),
        "state": "running",
    }
    registry.write_record(dashboard_path(), record)
    time.sleep(0.6)
    if not registry.pid_alive(process.pid):
        sys.exit(f"dashboard failed to start; see {log_path}")
    return record


def dashboard_stop() -> None:
    record = registry.read_record(dashboard_path())
    if record:
        registry.stop_process(record.get("pid"))
        record["state"] = "stopped"
        registry.write_record(dashboard_path(), record)


# ----------------------------------------------------------------- arms


def parse_difficulties(spec: str) -> list[int]:
    """Accepts '9', '1-9', or '1,3,5'."""
    spec = spec.strip()
    if "-" in spec and "," not in spec:
        low, high = spec.split("-", 1)
        return list(range(int(low), int(high) + 1))
    return [int(part) for part in spec.split(",") if part.strip()]


def running_ports() -> set[int]:
    return {
        record.get("port")
        for record in registry.all_runs()
        if record.get("state") == "running" and record.get("port")
    }


def pick_port(explicit: int | None, force: bool = False) -> int:
    """A free arm: a healthy backend with no run currently attached to it."""
    busy = running_ports()
    if explicit:
        if explicit in busy and not force:
            # Sharing a backend corrupts both runs' worlds and the results are
            # quietly wrong rather than obviously broken, so this refuses by
            # default even when the arm was named explicitly.
            sys.exit(
                f"a run is already using arm :{explicit}. Runs cannot share a "
                f"backend — the world state and the 'Hero' character are global "
                f"to it. Add an arm (herobench up --arms N) or pass --force if "
                f"you genuinely mean to."
            )
        return explicit
    candidates = sorted(
        record["port"]
        for record in registry.all_servers()
        if record.get("port") and env_server.health(record["port"])
    )
    for port in candidates:
        if port not in busy:
            return port
    if not candidates:
        sys.exit("no healthy backend — run: herobench up")
    sys.exit(
        f"every backend is busy ({sorted(busy)}). Add one: "
        f"herobench up --arms {len(candidates) + 1}"
    )


def cmd_run(args: argparse.Namespace) -> int:
    require_venv()
    port = pick_port(args.port, args.force)

    server = registry.read_record(env_server.server_record_path(port))
    if not server or not env_server.health(port):
        print(f"starting backend on :{port}")
        server = env_server.start(port, args.backend)
        if not env_server.health(port):
            sys.exit(f"backend on :{port} is not healthy; see {server.get('log_file')}")

    save_name = args.name or args.model.replace("/", "_")
    run_id = f"{save_name}-{time.strftime('%Y%m%d-%H%M%S')}"
    results_dir = args.results_dir
    results_file = str((REPO_ROOT / results_dir / f"{save_name}.json").resolve())

    key_env = SERVICE_KEY_ENV.get(args.service)
    if key_env and not os.environ.get(key_env) and not args.api_key:
        sys.exit(f"{args.service} needs an API key: export {key_env}=… (or pass --api-key)")

    record = {
        "id": run_id,
        "kind": "scoring_pipeline",
        "state": "starting",
        "repo_root": str(REPO_ROOT),
        "port": port,
        "backend": server.get("backend"),
        "model": args.model,
        "service": args.service,
        "save_name": save_name,
        "tasks_path": args.tasks,
        "prompts_path": args.prompts,
        "results_dir": results_dir,
        "results_file": results_file,
        "difficulties": parse_difficulties(args.diff),
        "task_num": args.task_num,
        "samples": args.samples,
        "timeout": args.timeout,
        "cutoff_actions": args.cutoff_actions,
        "resume": not args.no_resume,
        "overwrite_mode": args.overwrite_mode,
        # The key itself is never written to the record; only where to find it.
        "api_key_env": key_env,
        "llm_cfg": {
            "service": args.service,
            "model_name": args.model,
            "max_tokens": args.max_tokens,
            "reasoning_effort": args.reasoning_effort,
            "streaming": False,
            "thinking": args.thinking,
        },
        "created_at": time.time(),
        "started_at": time.time(),
    }

    record_path = registry.runs_dir() / f"{run_id}.json"
    log_path = registry.logs_dir() / f"{run_id}.log"
    record["log_file"] = str(log_path)
    registry.write_record(record_path, record)

    env = os.environ.copy()
    env["HEROBENCH_PORT"] = str(port)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SHIM_DIR), str(BENCH_DIR), str(REPO_ROOT)]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    if args.api_key and key_env:
        env[key_env] = args.api_key

    log_file = open(log_path, "ab", buffering=0)
    process = subprocess.Popen(
        [str(venv_python()), str(BENCH_DIR / "arm_runner.py"), str(record_path)],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    registry.update_record(record_path, pid=process.pid, state="running")

    print(f"run {run_id}")
    print(f"  arm      :{port} ({server.get('backend')})")
    print(f"  model    {args.model} via {args.service}")
    print(f"  results  {results_file}")
    print(f"  log      {log_path}")
    dash = registry.read_record(dashboard_path())
    if dash and registry.pid_alive(dash.get("pid")):
        print(f"  view     {dash['url']}")
    return 0


def cmd_up(args: argparse.Namespace) -> int:
    if not args.no_install:
        ensure_venv(with_llm=args.with_llm)

    if args.backend == "redis":
        # The backend calls flushdb() at startup, so standing up an arm ERASES
        # whatever is in the db it lands on. Default base 0 matches running the
        # backend by hand; --redis-db-base moves arms off a shared db.
        last = args.redis_db_base + args.arms - 1
        print(f"note: redis arms will flush db {args.redis_db_base}–{last} on startup")

    for index in range(args.arms):
        port = args.base_port + index
        print(f"backend :{port} ({args.backend})… ", end="", flush=True)
        record = env_server.start(port, args.backend, redis_db=args.redis_db_base + index)
        print("ok" if env_server.health(port) else f"FAILED — see {record.get('log_file')}")

    dash = dashboard_start(args.dash_port, args.dash_host)
    print(f"\nview  {dash['url']}")
    print("next  herobench run --model gpt-5-nano --service openai")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    servers = registry.all_servers()
    runs = registry.all_runs()

    print("BACKENDS")
    if not servers:
        print("  none")
    for record in servers:
        healthy = env_server.health(record["port"])
        print(
            f"  :{record['port']:<6} {record.get('backend','?'):<7} "
            f"{'healthy' if healthy else record.get('state','?')}"
        )

    print("\nRUNS")
    if not runs:
        print("  none")
    for record in sorted(runs, key=lambda r: -(r.get("started_at") or 0)):
        summary = progress.summarize(
            record.get("results_file"),
            record.get("expected_tasks"),
            record.get("difficulties"),
        )
        done = summary.get("completed", 0)
        expected = summary.get("expected") or "?"
        rate = summary.get("success_rate")
        score = summary.get("mean_score")
        rate_text = "—" if rate is None else f"{rate:.0f}%"
        score_text = "—" if score is None else f"{score:.1f}"
        now = f"  now {record['current_task']}" if record.get("current_task") else ""
        print(
            f"  {record.get('state', '?'):<9} {record.get('id', '?'):<34} "
            f":{record.get('port', '?')}  tasks {done}/{expected}  "
            f"succ {rate_text}  score {score_text}{now}"
        )

    dash = registry.read_record(dashboard_path())
    if dash and registry.pid_alive(dash.get("pid")):
        print(f"\nview  {dash['url']}")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    targets = []
    for record in registry.all_runs():
        if args.all or record.get("id") == args.run_id:
            if record.get("state") == "running":
                targets.append(record)
    if not targets:
        print("nothing running to stop")
        return 0
    for record in targets:
        registry.stop_process(record.get("pid"))
        registry.update_record(
            Path(record["_path"]), state="stopped", ended_at=time.time()
        )
        print(f"stopped {record['id']}")
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    for record in registry.all_runs():
        if record.get("state") == "running":
            registry.stop_process(record.get("pid"))
            registry.update_record(
                Path(record["_path"]), state="stopped", ended_at=time.time()
            )
            print(f"stopped run {record['id']}")
    for record in registry.all_servers():
        if registry.pid_alive(record.get("pid")):
            env_server.stop(record["port"])
            print(f"stopped backend :{record['port']}")
    dashboard_stop()
    print("stopped dashboard")
    return 0


def cmd_adopt(args: argparse.Namespace) -> int:
    """Register an agent this harness did not launch, so it joins the view."""
    run_id = f"{args.name}-{time.strftime('%Y%m%d-%H%M%S')}"
    record = {
        "id": run_id,
        "kind": "external",
        "state": "running",
        "save_name": args.name,
        "model": args.model,
        "service": args.service,
        "port": args.port,
        "pid": args.pid,
        "results_file": str(Path(args.results_file).resolve()) if args.results_file else None,
        "expected_tasks": args.expected,
        "started_at": time.time(),
        "heartbeat": time.time(),
    }
    registry.write_record(registry.runs_dir() / f"{run_id}.json", record)
    print(f"adopted {run_id}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    ok = True
    print(f"repo          {REPO_ROOT}")
    print(f"state         {registry.state_dir()}")
    print(f"venv          {'present' if venv_ready() else 'MISSING (herobench up)'}")
    if not venv_ready():
        ok = False
    else:
        probe = subprocess.run(
            [str(venv_python()), "-c",
             "import fastapi, sqlmodel, pydantic, requests;"
             "print(pydantic.VERSION)"],
            capture_output=True, text=True,
        )
        if probe.returncode == 0:
            version = probe.stdout.strip()
            good = tuple(int(p) for p in version.split(".")[:2]) < (2, 12)
            print(f"pydantic      {version} {'ok' if good else 'TOO NEW — breaks sqlmodel 0.0.24'}")
            ok = ok and good
        else:
            print(f"deps          MISSING — {probe.stderr.strip().splitlines()[-1:]}")
            ok = False
    print(f"uv            {shutil.which('uv') or 'not found (venv module fallback)'}")

    redis_ok = subprocess.run(
        ["redis-cli", "ping"], capture_output=True, text=True
    ).stdout.strip() == "PONG" if shutil.which("redis-cli") else False
    print(f"redis         {'up' if redis_ok else 'not reachable (only needed for --backend redis)'}")

    for record in registry.all_servers():
        print(f"backend :{record['port']}  {'healthy' if env_server.health(record['port']) else 'down'}")
    return 0 if ok else 1


# ----------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="herobench", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="bootstrap venv, start backends and the dashboard")
    up.add_argument("--arms", type=int, default=1, help="how many backends (default 1)")
    up.add_argument("--base-port", type=int, default=DEFAULT_BASE_PORT)
    up.add_argument("--backend", choices=["sqlite", "redis"], default="sqlite")
    up.add_argument("--redis-db-base", type=int, default=0,
                    help="first redis db index for --backend redis; arms take "
                         "consecutive indices and FLUSH them at startup")
    up.add_argument("--dash-port", type=int, default=DEFAULT_DASH_PORT)
    up.add_argument("--dash-host", default="0.0.0.0")
    up.add_argument("--with-llm", action="store_true", help="also install torch/transformers/ollama")
    up.add_argument("--no-install", action="store_true", help="skip dependency install")
    up.set_defaults(func=cmd_up)

    run = sub.add_parser("run", help="launch a benchmark arm")
    run.add_argument("--model", required=True, help="model id passed to LLMService")
    run.add_argument("--service", default="openai",
                     choices=["openai", "openrouter", "openrouter_openai", "ollama", "hf"])
    run.add_argument("--name", help="save name / results filename (default: model id)")
    run.add_argument("--port", type=int, help="pin to this arm; default is the first free one")
    run.add_argument("--backend", choices=["sqlite", "redis"], default="sqlite")
    run.add_argument("--diff", default="1-9", help="'9', '1-9' or '1,3,5'")
    run.add_argument("--task-num", default="all", help="'all', '12', or '12-18'")
    run.add_argument("--tasks", default="datasets/dataset_tasks.json")
    run.add_argument("--prompts", default="datasets/dataset_prompts.json")
    run.add_argument("--results-dir", default="results/results_base")
    run.add_argument("--samples", type=int, default=1)
    run.add_argument("--timeout", type=int, default=100)
    run.add_argument("--cutoff-actions", type=int, default=4000)
    run.add_argument("--max-tokens", type=int, default=40000)
    run.add_argument("--reasoning-effort", default="high", choices=["low", "medium", "high"])
    run.add_argument("--thinking", action="store_true")
    run.add_argument("--no-resume", action="store_true")
    run.add_argument("--overwrite-mode", default="none", choices=["all", "lose", "0", "none"])
    run.add_argument("--api-key", help="overrides the service's key environment variable")
    run.add_argument("--force", action="store_true",
                     help="attach to --port even if a run is already using it "
                          "(runs sharing a backend corrupt each other)")
    run.set_defaults(func=cmd_run)

    status = sub.add_parser("status", help="text view of backends and runs")
    status.set_defaults(func=cmd_status)

    stop = sub.add_parser("stop", help="stop a run")
    stop.add_argument("run_id", nargs="?")
    stop.add_argument("--all", action="store_true")
    stop.set_defaults(func=cmd_stop)

    down = sub.add_parser("down", help="stop runs, backends and dashboard")
    down.set_defaults(func=cmd_down)

    adopt = sub.add_parser("adopt", help="register an externally-launched agent")
    adopt.add_argument("--name", required=True)
    adopt.add_argument("--model", default="unknown")
    adopt.add_argument("--service", default="external")
    adopt.add_argument("--port", type=int, help="the backend it drives, for the live action feed")
    adopt.add_argument("--pid", type=int, help="so the view can tell when it exits")
    adopt.add_argument("--results-file", help="a scoring_pipeline-shaped results json, if it writes one")
    adopt.add_argument("--expected", type=int, help="expected task count, for the progress bar")
    adopt.set_defaults(func=cmd_adopt)

    doctor = sub.add_parser("doctor", help="check the environment")
    doctor.set_defaults(func=cmd_doctor)

    dash = sub.add_parser("dash", help="start the dashboard alone")
    dash.add_argument("--port", type=int, default=DEFAULT_DASH_PORT)
    dash.add_argument("--host", default="0.0.0.0")
    dash.set_defaults(func=lambda a: (print(dashboard_start(a.port, a.host)["url"]), 0)[1])

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
