"""Run with python -m mkw_tracker."""

import argparse
import getpass
import ipaddress
import os
import sys
import tempfile
from pathlib import Path

from .server import Application, make_server
from .name_engines import NAME_ENGINES
from .diagnostics import configure_logging, log_event


def seed_demo(store):
    """Manually verified sample reference. No OCR or live capture is implied."""
    names = ["DxDMilkies", "uwu rawr", "ST Jirachi", "z17chopper", "MiSt Doent", "MiSt Zeke",
             "zRiasEve", "Japanese name A (verify spelling)", "ST avid (verify decoration)",
             "Japanese name B (verify spelling)", "DxD Nyx098", "uwu meow"]
    players = [store.add_player(name)["id"] for name in names]
    recording = store.create_recording("Edited sample; coarse intro markers")
    for course, offset, order in [
            ("Whistlestop Summit", 56000, range(12)),
            ("Wario Stadium", 180000, [4, 7, 11, 2, 3, 6, 9, 1, 5, 0, 10, 8])]:
        race = store.create_race(course, course, recording["id"], offset)
        store.submit_result(race["id"], [players[i] for i in order])
    store.create_race("unfinished-sample-race", "Bowser's Castle", recording["id"], 286000)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='backslashreplace')
    parser = argparse.ArgumentParser(description="Mario Kart World local scoreboard and capture review")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind IP address or local hostname (default: 127.0.0.1). "
                             "Non-loopback binding exposes an unauthenticated HTTP dashboard.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path(".local/tracker"))
    parser.add_argument("--demo", action="store_true", help="Use temporary manually verified sample scores")
    parser.add_argument("--obs-source", help="Exact OBS input or scene name to capture")
    parser.add_argument("--obs-url", default="ws://127.0.0.1:4455")
    parser.add_argument("--obs-password-prompt", action="store_true", help="Read the OBS WebSocket password without echoing it")
    parser.add_argument("--tessdata", type=Path, default=Path(".local/tessdata"))
    parser.add_argument("--name-engine", choices=sorted(NAME_ENGINES), default='auto',
                        help="Player-name OCR; auto selects local Qwen with GPU memory, then Paddle, then Tesseract")
    parser.add_argument("--verbose", action="store_true",
                        help="Write extra diagnostic detail to DATA_DIR/logs/ (rotating component files); "
                             "stdout keeps concise warnings and errors")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    if args.demo and args.obs_source:
        parser.error("demo uses temporary sample data; run capture without --demo")
    temporary = None
    diagnostics = None
    try:
        temporary = tempfile.TemporaryDirectory(prefix="mkw-demo-") if args.demo else None
        data_dir = Path(temporary.name) if temporary else args.data_dir
        diagnostics = configure_logging(data_dir, verbose=args.verbose)
        _run(args, parser, data_dir)
    except KeyboardInterrupt:
        pass
    except Exception as error:
        if diagnostics is not None:
            log_event('app', 'failed', error=error)
        else:
            print("ERROR [app] Cannot prepare tracker data/logs; check folder access and free space.", flush=True)
        raise SystemExit(1) from None
    finally:
        cleanup_failed = False
        # Windows cannot remove demo log files until their handlers are closed.
        if diagnostics is not None:
            try:
                diagnostics.close()
            except Exception:
                cleanup_failed = True
                print("ERROR [app] Cannot close diagnostic logs; check folder access and free space.", flush=True)
        if temporary is not None:
            try:
                temporary.cleanup()
            except Exception:
                cleanup_failed = True
                print("ERROR [app] Cannot clean up temporary demo data; check folder access.", flush=True)
        if cleanup_failed:
            raise SystemExit(1) from None


def _run(args, parser, data_dir):
    app = None
    server = None
    try:
        app = Application(data_dir / "tracker.sqlite3", data_dir, mode="demo" if args.demo else "manual")
        app.import_tessdata = args.tessdata
        try:
            server = make_server(app, args.port, host=args.host)
        except (OSError, ValueError) as error:
            log_event('http', 'bind_failed', error=error, port=args.port)
            parser.error("cannot listen; check the bind address and whether the port is already in use")
        bound_host, bound_port = server.server_address[:2]
        log_event('http', 'listening', port=bound_port)
        address = ipaddress.ip_address(bound_host)
        if not address.is_loopback:
            print("WARNING: Network access has no login or TLS. Anyone who can reach this port "
                  "can change/delete data and start capture using saved OBS credentials. "
                  "Restrict access with a firewall or private tunnel; never expose it to the public Internet.",
                  file=sys.stderr, flush=True)
        if args.demo:
            seed_demo(app.store)
        if args.obs_source:
            from .obs import ObsClient
            # Reuse the selected mode's coordinator and state file. A saved WW
            # selection must never load/prune the separate Mogi review queue.
            app.capture.reader = app.imports.reader_factory(args.name_engine)
            password = getpass.getpass("OBS WebSocket password: ") if args.obs_password_prompt else os.environ.get("OBS_PASSWORD", "")
            app.capture.start(ObsClient(args.obs_url, password), args.obs_source)
        browser_host = ("::1" if address.version == 6 else "127.0.0.1") if address.is_unspecified else bound_host
        browser_host = f"[{browser_host}]" if ":" in browser_host else browser_host
        print(f"Race tracker: http://{browser_host}:{bound_port}", flush=True)
        if address.is_unspecified:
            print(f"Listening on {bound_host} port {bound_port}; remote clients use this machine's network address.",
                  flush=True)
        print("Demo reference data" if args.demo else f"Data: {data_dir.resolve()}", flush=True)
        log_event('app', 'started')
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        cleanup_failed = False
        for resource in (server, app):
            if resource is not None:
                try:
                    if resource is server:
                        resource.server_close()
                    else:
                        resource.close()
                except Exception as error:
                    cleanup_failed = True
                    log_event('app', 'cleanup_failed', error=error)
        if cleanup_failed:
            raise SystemExit(1) from None
        log_event('app', 'stopped')


if __name__ == "__main__":
    main()
