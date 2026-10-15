"""python -m newsletter_video.ui  -  starts the control panel and opens it in your browser."""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

from .config import PROJECT_ROOT, UIPaths
from .server import make_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m newsletter_video.ui", description=__doc__)
    parser.add_argument("--port", type=int, default=8765, help="preferred port (default 8765; another is used if busy)")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    parser.add_argument("--env-file", type=Path, default=None,
                        help="settings file to edit (default: .env in the project)")
    args = parser.parse_args(argv)

    paths = UIPaths.default(PROJECT_ROOT, env_path=args.env_file.resolve() if args.env_file else None)
    server, app = make_server(paths, port=args.port)
    url = f"http://127.0.0.1:{app.guard.port}/"
    print(f"ByteSize control panel running at {url}")
    print("Only this computer can reach it. Press Ctrl+C to stop; runs already started keep going.")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
