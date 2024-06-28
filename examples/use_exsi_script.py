"""
This is an example of how to use the exsi_client module individually to control the scanner without the rest of the shim tool.
Note: all of the conditional variables that are used to wait on particular events that the exsi client is doing in different events.
"""

import json
import os
import queue
import sys

# Add the parent directory to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.abspath(""), "..")))

from shimTool.exsi_client import exsi


def load_config(filename):
    with open(filename, "r") as file:
        return json.load(file)


def main():
    # Load config file
    config = load_config("../config.json")

    # Make a sample EXSI class instance
    # The requireExsiConnection decorator will check if the connection is ready before executing any exsi functionality.
    exsi_instance = exsi(config)
    exsi_instance.connected_ready_event.wait()

    # Load protocol and get task keys
    protocol_name = "BPT_EXSI"
    exsi_instance.sendLoadProtocol(protocol_name)
    exsi_instance.send_event.wait()
    exsi_instance.ready_event.wait()
    print(f"tasks: {exsi_instance.taskKeys}")

    print("Localizer scan loaded")
    # Run localizer
    exsi_instance.sendSelTask()
    exsi_instance.sendActTask()
    exsi_instance.sendPatientTable()
    exsi_instance.sendScan()
    print("Localizer scan started")

    exsi_instance.images_ready_event.wait()
    print("Localizer scan done")


if __name__ == "__main__":
    main()
