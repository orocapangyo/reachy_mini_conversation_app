"""Entrypoint for the Reachy Mini conversation app."""

from __future__ import annotations
import sys
import time
import signal
import asyncio
import logging
import argparse
import threading
from typing import TYPE_CHECKING, Any, Optional
from pathlib import Path
from collections.abc import Callable, Awaitable

from fastapi import FastAPI, Request, Response

from reachy_mini import ReachyMini, ReachyMiniApp
from reachy_mini_conversation_app import app_lifecycle
from reachy_mini_conversation_app.utils import (
    parse_args,
    setup_logger,
    log_connection_troubleshooting,
)


if TYPE_CHECKING:
    from reachy_mini_conversation_app.console import LocalStream


def _start_inactivity_timeout_thread(
    timeout_minutes: float,
    stream_manager: LocalStream,
    logger: logging.Logger,
    app_stop_event: threading.Event | None,
    go_to_sleep: Callable[[], dict[str, Any]] | None = None,
) -> threading.Thread:
    """Start a daemon that puts the app to sleep after inactivity."""
    timeout_seconds = timeout_minutes * 60.0

    def poll_inactivity_timeout() -> None:
        logger.info("App inactivity timeout enabled: %.1f minutes.", timeout_minutes)
        while app_stop_event is None or not app_stop_event.is_set():
            elapsed = stream_manager.seconds_since_activity()
            if elapsed >= timeout_seconds:
                logger.info("No activity for %.1f minutes; going to sleep.", elapsed / 60.0)
                try:
                    if go_to_sleep is not None:
                        go_to_sleep()
                    else:
                        stream_manager.close()
                except Exception as e:
                    logger.error("Error while going to sleep after inactivity timeout: %s", e)
                    try:
                        stream_manager.close()
                    except Exception as close_error:
                        logger.error("Error while closing stream manager after inactivity timeout: %s", close_error)
                return
            time.sleep(1.0)

    thread = threading.Thread(target=poll_inactivity_timeout, daemon=True)
    thread.start()
    return thread


def main() -> None:
    """Entrypoint for the Reachy Mini conversation app."""
    args, _ = parse_args()
    if args.command == "tool-spaces":
        from reachy_mini_conversation_app.tool_spaces import handle_tool_spaces_command

        logger = setup_logger(args.debug)
        try:
            raise SystemExit(handle_tool_spaces_command(args))
        except Exception as exc:
            logger.error("tool-spaces command failed: %s", exc)
            raise SystemExit(1) from exc
    run(args)


def run(
    args: argparse.Namespace,
    robot: ReachyMini = None,
    app_stop_event: Optional[threading.Event] = None,
    settings_app: Optional[FastAPI] = None,
    instance_path: Optional[str] = None,
) -> None:
    """Run the Reachy Mini conversation app."""
    # Putting these dependencies here makes the dashboard faster to load when the conversation app is installed
    from reachy_mini_conversation_app.moves import MovementManager
    from reachy_mini_conversation_app.config import (
        GEMINI_BACKEND,
        OPENAI_BACKEND,
        HF_LOCAL_CONNECTION_MODE,
        config,
        set_instance_path,
        get_conversation_backend,
        resolve_daemon_connection,
        get_hf_connection_selection,
        resolve_app_timeout_minutes,
        refresh_runtime_config_from_env,
    )
    from reachy_mini_conversation_app.startup_settings import (
        StartupSettings,
        load_startup_settings_into_runtime,
    )

    logger = setup_logger(args.debug)
    logger.info("Starting Reachy Mini Conversation App")
    if getattr(args, "profile", None):
        config.REACHY_MINI_CUSTOM_PROFILE = args.profile
        logger.info("Using profile from CLI argument: %s", args.profile)
    set_instance_path(instance_path)
    startup_settings = StartupSettings()

    if instance_path is not None:
        try:
            from dotenv import load_dotenv

            env_path = Path(instance_path) / ".env"
            if env_path.exists():
                load_dotenv(dotenv_path=str(env_path), override=True)
                refresh_runtime_config_from_env()
                logger.info("Loaded instance configuration from %s", env_path)
        except Exception as e:
            logger.warning("Failed to load instance configuration: %s", e)

        try:
            startup_settings = load_startup_settings_into_runtime(instance_path)
        except Exception as e:
            logger.warning("Failed to load startup settings: %s", e)

    if get_conversation_backend() == GEMINI_BACKEND:
        logger.info("Configured Gemini Live backend, model: %s", config.GEMINI_LIVE_MODEL)
        if not (config.GEMINI_API_KEY or "").strip():
            logger.error(
                "CONVERSATION_BACKEND=gemini requires GEMINI_API_KEY. Set it in the environment or .env and restart."
            )
            sys.exit(1)
    elif get_conversation_backend() == OPENAI_BACKEND:
        logger.info("Configured OpenAI realtime backend, model: %s", config.OPENAI_REALTIME_MODEL)
        if not (config.OPENAI_API_KEY or "").strip():
            logger.error(
                "CONVERSATION_BACKEND=openai requires OPENAI_API_KEY. Set it in the environment or .env and restart."
            )
            sys.exit(1)
    else:
        logger.info(
            "Configured Hugging Face realtime backend, connection mode: %s",
            get_hf_connection_selection().mode,
        )

    from reachy_mini_conversation_app.console import LocalStream
    from reachy_mini_conversation_app.tools.core_tools import ToolDependencies, initialize_tools
    from reachy_mini_conversation_app.conversation_handler import ConversationHandler

    if robot is None:
        try:
            robot_kwargs: dict[str, Any] = {}
            if args.robot_name is not None:
                robot_kwargs["robot_name"] = args.robot_name

            daemon_connection = resolve_daemon_connection(config.REACHY_MINI_HOST, config.REACHY_MINI_PORT)
            if daemon_connection.connection_mode is not None:
                robot_kwargs["connection_mode"] = daemon_connection.connection_mode
            if daemon_connection.host is not None:
                robot_kwargs["host"] = daemon_connection.host
            robot_kwargs["port"] = daemon_connection.port
            daemon_freshly_started = False
            if daemon_connection.connection_mode == "network" and daemon_connection.host is not None:
                from reachy_mini_conversation_app.daemon_autostart import ensure_remote_daemon_running

                daemon_freshly_started = (
                    ensure_remote_daemon_running(
                        daemon_connection.host,
                        daemon_connection.port,
                        auto_start=config.REACHY_MINI_AUTO_START_DAEMON,
                    )
                    == "started"
                )
            logger.info(
                "Connecting to Reachy Mini daemon (%s, %s:%d)",
                daemon_connection.connection_mode or "auto",
                daemon_connection.host or "localhost",
                daemon_connection.port,
            )
            # A freshly started daemon needs a few seconds to register its media
            # producer; retry until the connection actually succeeds.
            connect_attempts = 4 if daemon_freshly_started else 1
            for attempt in range(1, connect_attempts + 1):
                try:
                    robot = ReachyMini(**robot_kwargs)
                    break
                except Exception:
                    if attempt == connect_attempts:
                        raise
                    logger.info(
                        "Daemon not fully ready yet (attempt %d/%d); retrying in 5 s...",
                        attempt,
                        connect_attempts,
                    )
                    time.sleep(5.0)

        except TimeoutError as e:
            logger.error(f"Connection timeout: Failed to connect to Reachy Mini daemon. Details: {e}")
            log_connection_troubleshooting(logger, args.robot_name)
            sys.exit(1)

        except ConnectionError as e:
            logger.error(f"Connection failed: Unable to establish connection to Reachy Mini. Details: {e}")
            log_connection_troubleshooting(logger, args.robot_name)
            sys.exit(1)

        except Exception as e:
            logger.error(f"Unexpected error during robot initialization: {type(e).__name__}: {e}")
            logger.error("Please check your configuration and try again.")
            sys.exit(1)

    app_lifecycle.wake_up_if_sleeping(robot, logger)

    movement_manager = MovementManager(current_robot=robot)

    deps = ToolDependencies(
        reachy_mini=robot,
        movement_manager=movement_manager,
        instance_path=instance_path,
        camera_enabled=not args.no_camera,
    )

    def build_handler(startup_voice: Optional[str] = None) -> ConversationHandler:
        """Build the realtime conversation handler for the configured backend."""
        if get_conversation_backend() == GEMINI_BACKEND:
            from reachy_mini_conversation_app.gemini_realtime import GeminiRealtimeHandler

            logger.info("Using Gemini Live handler (model=%s)", config.GEMINI_LIVE_MODEL)
            return GeminiRealtimeHandler(
                deps,
                instance_path=instance_path,
                startup_voice=startup_voice,
            )

        if get_conversation_backend() == OPENAI_BACKEND:
            from reachy_mini_conversation_app.openai_realtime import OpenAIRealtimeHandler

            logger.info("Using OpenAI realtime handler (model=%s)", config.OPENAI_REALTIME_MODEL)
            return OpenAIRealtimeHandler(
                deps,
                instance_path=instance_path,
                startup_voice=startup_voice,
            )

        from reachy_mini_conversation_app.huggingface_realtime import HuggingFaceRealtimeHandler

        hf_connection_selection = get_hf_connection_selection()
        transport_label = (
            "Hugging Face direct websocket"
            if hf_connection_selection.mode == HF_LOCAL_CONNECTION_MODE and hf_connection_selection.has_target
            else "Hugging Face session proxy"
        )
        logger.info("Using Hugging Face realtime handler (%s)", transport_label)
        return HuggingFaceRealtimeHandler(
            deps,
            instance_path=instance_path,
            startup_voice=startup_voice,
        )

    handler = build_handler(startup_settings.voice)

    stream_manager: LocalStream | None = None
    own_ui_server = None

    effective_settings_app = settings_app
    if args.ui and settings_app is None:
        effective_settings_app = FastAPI()

        @effective_settings_app.middleware("http")
        async def _no_cache(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
            """Serve everything no-store so browsers don't keep stale UI modules."""
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response

    stream_manager = LocalStream(
        handler,
        robot,
        settings_app=effective_settings_app,
        instance_path=instance_path,
        handler_factory=build_handler,
        startup_voice=startup_settings.voice,
    )

    # The page is served immediately, so the API must be live before the slow startup work below.
    if effective_settings_app is not None:
        stream_manager._init_settings_ui_if_needed()

    go_to_sleep_lock = threading.Lock()
    go_to_sleep_requested = threading.Event()

    def go_to_sleep_and_stop_app() -> dict[str, Any]:
        """Put Reachy to sleep, then stop the current app."""
        if not go_to_sleep_lock.acquire(blocking=False):
            return {"status": "already_requested"}

        try:
            if go_to_sleep_requested.is_set():
                return {"status": "already_requested"}
            go_to_sleep_requested.set()

            logger.info("Going to sleep before stopping conversation app.")
            sleep_error: str | None = None

            try:
                robot.disable_wobbling()
            except Exception as e:
                logger.debug("Error disabling wobbling before sleep: %s", e)

            movement_manager.stop(reset_to_neutral=False)

            try:
                robot.goto_sleep()
            except Exception as e:
                sleep_error = f"{type(e).__name__}: {e}"
                logger.error("Failed to move Reachy Mini to sleep pose: %s", e)

            stop_current_app_requested = False
            if app_stop_event is None or not app_stop_event.is_set():
                stop_current_app_requested = app_lifecycle.request_stop_current_app(robot, logger)
            local_stop_requested = True
            if app_stop_event is not None:
                app_stop_event.set()
            else:
                try:
                    stream_manager.close()
                except Exception as e:
                    local_stop_requested = False
                    logger.error("Error while closing stream manager after go_to_sleep: %s", e)

            result: dict[str, Any] = {
                "status": "sleeping" if sleep_error is None else "stop_requested",
                "stop_current_app_requested": stop_current_app_requested,
                "local_stop_requested": local_stop_requested,
            }
            if sleep_error is not None:
                result["error"] = f"go_to_sleep movement failed: {sleep_error}"
            return result
        finally:
            go_to_sleep_lock.release()

    def go_to_sleep_action() -> dict[str, Any]:
        """Route voice go_to_sleep to standby (wake-word wait) or the legacy app stop.

        Standby keeps the app and realtime session alive so a wake phrase can
        resume the conversation; only handlers that support it (OpenAI backend)
        and REACHY_MINI_STANDBY_ON_SLEEP=1 take that path.
        """
        active_handler = getattr(stream_manager, "handler", None) or handler
        if config.REACHY_MINI_STANDBY_ON_SLEEP and hasattr(active_handler, "request_standby"):
            standby_result: dict[str, Any] = active_handler.request_standby()
            return standby_result
        return go_to_sleep_and_stop_app()

    deps.go_to_sleep = go_to_sleep_action

    def run_go_to_sleep_tool() -> dict[str, Any]:
        return app_lifecycle.run_go_to_sleep_tool(deps, logger)

    sound_watcher = None
    if config.REACHY_MINI_DOA_LOOK:
        from reachy_mini_conversation_app.sound_direction import SpeakerGaze, SoundDirectionWatcher

        def _doa_gaze_enabled() -> bool:
            """Gaze only while awake; standby records direction without moving."""
            active_handler = getattr(stream_manager, "handler", None) or handler
            return not bool(getattr(active_handler, "in_standby", False))

        doa_target = resolve_daemon_connection(config.REACHY_MINI_HOST, config.REACHY_MINI_PORT)
        speaker_gaze = SpeakerGaze(movement_manager, robot, is_enabled=_doa_gaze_enabled)
        sound_watcher = SoundDirectionWatcher(
            doa_target.host or "localhost",
            doa_target.port,
            on_speech=speaker_gaze.on_speech,
        )
        sound_watcher.start()
        if hasattr(handler, "sound_watcher"):
            # Class attribute so UI-triggered handler rebuilds inherit the watcher.
            type(handler).sound_watcher = sound_watcher

    if args.ui and settings_app is None and effective_settings_app is not None:
        import uvicorn

        own_ui_server = uvicorn.Server(
            uvicorn.Config(effective_settings_app, host="0.0.0.0", port=7860, log_level="warning")
        )
        threading.Thread(target=own_ui_server.run, daemon=True, name="ui-server").start()
        logger.info("Web UI available at http://localhost:7860")

    try:
        initialize_tools(instance_path=instance_path)
    except Exception as e:
        logger.error("Failed to initialize tools: %s", e)
        sys.exit(1)

    # Each async service → its own thread/loop
    movement_manager.start()
    # Audio-reactive head motion is driven by the daemon's wobbler, which
    # taps the media pipeline at push_audio_sample. The console stream pushes
    # assistant audio through that pipeline directly.
    robot.enable_wobbling()

    timeout_minutes = resolve_app_timeout_minutes()
    if timeout_minutes is not None:
        _start_inactivity_timeout_thread(timeout_minutes, stream_manager, logger, app_stop_event, run_go_to_sleep_tool)

    def poll_stop_event() -> None:
        """Poll the stop event to allow graceful shutdown.

        Deliberately does NOT put the robot to sleep: an external stop
        (mobile app, dashboard, app switch) means "stop this app", not
        "power the robot down" — the daemon returns it to the neutral
        pose afterwards, awake and ready for the next app. Sleeping is
        reserved for the explicit paths (the voice go_to_sleep tool and
        the inactivity timeout).
        """
        if app_stop_event is not None:
            app_stop_event.wait()

        logger.info("App stop event detected, shutting down...")
        try:
            stream_manager.close()
        except Exception as e:
            logger.error(f"Error while closing stream manager: {e}")

    if app_stop_event:
        threading.Thread(target=poll_stop_event, daemon=True).start()

    # Route Windows Ctrl+Break / console close through the same path as Ctrl+C
    # so sleep-on-exit also covers them.
    if hasattr(signal, "SIGBREAK"):

        def _raise_keyboard_interrupt(_signum: int, _frame: object) -> None:
            raise KeyboardInterrupt

        signal.signal(signal.SIGBREAK, _raise_keyboard_interrupt)

    keyboard_interrupted = False
    try:
        stream_manager.launch()
    except KeyboardInterrupt:
        keyboard_interrupted = True
        logger.info("Keyboard interruption in main thread... closing server.")
    finally:
        # Direct console exits put the robot to sleep (opt-out via
        # REACHY_MINI_SLEEP_ON_EXIT=0). External stops (dashboard/mobile)
        # keep the original behavior: stop the app, leave the robot awake.
        # In standby the robot is already in the sleep pose — nothing to do.
        active_handler = getattr(stream_manager, "handler", None) or handler
        handler_in_standby = bool(getattr(active_handler, "in_standby", False))
        if (
            keyboard_interrupted
            and config.REACHY_MINI_SLEEP_ON_EXIT
            and not go_to_sleep_requested.is_set()
            and not handler_in_standby
        ):
            logger.info("Sleep-on-exit: running go_to_sleep before shutdown...")
            run_go_to_sleep_tool()

        if sound_watcher is not None:
            sound_watcher.stop()

        if own_ui_server is not None:
            own_ui_server.should_exit = True

        # Stop the motion writes without changing the robot's posture. If
        # the shutdown came from the voice go_to_sleep tool the robot is
        # already in the sleep pose; on a plain stop it stays awake and
        # the daemon returns it to neutral once the process exits.
        movement_manager.stop(reset_to_neutral=False)
        try:
            robot.disable_wobbling()
        except Exception as e:
            logger.debug(f"Error disabling wobbling during shutdown: {e}")

        # Ensure media is explicitly closed before disconnecting
        try:
            robot.media.close()
        except Exception as e:
            logger.debug(f"Error closing media during shutdown: {e}")

        # prevent connection to keep alive some threads
        robot.client.disconnect()
        time.sleep(1)
        logger.info("Shutdown complete.")


class ReachyMiniConversationApp(ReachyMiniApp):  # type: ignore[misc]
    """Reachy Mini Apps entry point for the conversation app."""

    custom_app_url = "http://0.0.0.0:7860/"
    dont_start_webserver = False

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        """Run the Reachy Mini conversation app."""
        asyncio.set_event_loop(asyncio.new_event_loop())

        args, _ = parse_args()

        instance_path = self._get_instance_path().parent
        run(
            args,
            robot=reachy_mini,
            app_stop_event=stop_event,
            settings_app=self.settings_app,
            instance_path=instance_path,
        )


if __name__ == "__main__":
    app = ReachyMiniConversationApp()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()
