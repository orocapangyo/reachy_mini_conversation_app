"""``python -m jisu.dashboard``: launch the simulator + conversation app and open the dashboard."""

from __future__ import annotations
import logging
import argparse
import threading
import webbrowser
from pathlib import Path

import uvicorn

from jisu.dashboard.server import create_app
from jisu.dashboard.launcher import LaunchSettings, SimulationLauncher
from jisu.dashboard.constants import (
    APP_UI_PORT,
    DAEMON_PORT,
    DASHBOARD_HOST,
    DASHBOARD_PORT,
    DEFAULT_PROFILE,
)
from jisu.dashboard.log_buffer import LogBuffer
from jisu.dashboard.memory.registry import available_backends, selected_backend_name


BROWSER_OPEN_DELAY_S = 1.5


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse dashboard CLI arguments."""
    backend_names = [info.name for info in available_backends()]
    parser = argparse.ArgumentParser(prog="python -m jisu.dashboard", description=__doc__)
    parser.add_argument("--host", default=DASHBOARD_HOST, help="dashboard bind address")
    parser.add_argument("--port", type=int, default=DASHBOARD_PORT, help="dashboard port")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="conversation app profile")
    parser.add_argument(
        "--backend",
        default=selected_backend_name(),
        choices=backend_names,
        help="long-term memory backend for the conversation app",
    )
    parser.add_argument(
        "--conversation-backend",
        default=None,
        help="override CONVERSATION_BACKEND (openai/gemini/hf); default keeps .env",
    )
    parser.add_argument("--headless", action="store_true", help="run the MuJoCo daemon without its 3D window")
    parser.add_argument("--no-start", action="store_true", help="do not start the app automatically")
    parser.add_argument("--no-browser", action="store_true", help="do not open the dashboard in a browser")
    parser.add_argument("--open-app-ui", action="store_true", help="also open the app's own UI (7860)")
    parser.add_argument("--debug", action="store_true", help="run the conversation app with --debug")
    parser.add_argument("--daemon-port", type=int, default=DAEMON_PORT)
    parser.add_argument("--app-port", type=int, default=APP_UI_PORT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Run the dashboard until Ctrl+C, then stop the processes it started."""
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

    logs = LogBuffer()
    settings = LaunchSettings(
        profile=args.profile,
        headless=args.headless,
        daemon_port=args.daemon_port,
        app_port=args.app_port,
        memory_backend=args.backend,
        conversation_backend=args.conversation_backend,
        debug=args.debug,
    )
    launcher = SimulationLauncher(settings, logs, cwd=Path.cwd())
    app = create_app(launcher, logs, app_port=args.app_port)
    dashboard_url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}/"

    def _boot() -> None:
        if not args.no_start:
            try:
                launcher.start_app()
            except RuntimeError as exc:
                logs.append("dashboard", f"ERROR: startup failed: {exc}")
        if args.open_app_ui:
            webbrowser.open(f"http://localhost:{args.app_port}/#/")

    if not args.no_browser:
        threading.Timer(BROWSER_OPEN_DELAY_S, webbrowser.open, args=(dashboard_url,)).start()
    threading.Thread(target=_boot, name="dashboard-boot", daemon=True).start()

    print(f"Simulator dashboard: {dashboard_url}  (Ctrl+C to stop)", flush=True)
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level="warning"))
    try:
        server.run()
    finally:
        print("Stopping conversation app and simulator...", flush=True)
        launcher.shutdown()


if __name__ == "__main__":
    main()
