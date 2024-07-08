import argparse
import code
import json
import signal
import subprocess
import sys

from PyQt6.QtWidgets import QApplication

from shimTool.Gui import Gui
from shimTool.Tool import Tool
from shimTool.utils import kickoff_thread


def handle_exit(signal_received, frame):
    # Handle any cleanup here
    print("SIGINT or CTRL-C detected. Trying to exit gracefully.")
    QApplication.quit()


def load_config(filename):
    with open(filename, "r") as file:
        return json.load(file)


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Launch the app with or without GUI.")
    parser.add_argument("--no-gui", action="store_true", help="Launch python cli version")
    parser.add_argument("--quiet", action="store_true", help="Launch it without as much logging")
    parser.add_argument(
        "--config",
        type=str,
        default="config.json",
        help="Path to the configuration file (default: config.json)",
    )

    args = parser.parse_args()

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    print(f"Starting shimTool with {'no gui' if args.no_gui else 'gui'} and {'quiet' if args.quiet else 'verbose'}")

    config = load_config(args.config)

    if not args.no_gui:
        gui = Gui(config, not args.quiet)
    else:
        tool = Tool(config, debugging=not args.quiet)
        code.interact(local=globals())

