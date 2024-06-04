"""
so this nearly works, it just does not pipe the output of the exsi client to the parent process properly. 
however, it does look like it is successful in letting you call the exsi client from the parent process and run tasks.
"""


import logging
from shimTool.exsi_client import exsi
import json
import multiprocessing
import threading
import os
import sys
import time
from queue import Empty  # Correct import for Empty exception

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)s] %(message)s')

def start_exsi_client():
    request_queue = multiprocessing.Queue()
    response_queue = multiprocessing.Queue()
    parent_pipe, child_pipe = multiprocessing.Pipe()
    stop_event = multiprocessing.Event()

    config = {}
    currentPath = os.path.dirname(os.path.realpath(__file__))
    parentPath = os.path.dirname(currentPath)
    configPath = os.path.join(parentPath, 'config.json')
    config = load_config(configPath)

    process = multiprocessing.Process(target=run_exsi_client, args=(config, request_queue, response_queue, child_pipe, stop_event))
    process.start()

    return process, request_queue, response_queue, parent_pipe, stop_event

def run_exsi_client(config, request_queue, response_queue, pipe, stop_event):
    logging.debug("Starting exsi_client process")
    try:
        # Redirect stdout and stderr to the pipe
        os.dup2(pipe.fileno(), sys.stdout.fileno())
        os.dup2(pipe.fileno(), sys.stderr.fileno())

        exsi_client = exsi(config)
        logging.debug("Exsi client created")

        exsi_client.connected_ready_event.wait()
        logging.debug("Exsi client connected and ready")

        while not stop_event.is_set():
            try:
                method, args, kwargs = request_queue.get(timeout=1)
                logging.debug(f"Received method call: {method} with args: {args} and kwargs: {kwargs}")
                if method:
                    result = getattr(exsi_client, method)(*args, **kwargs)
                    response_queue.put(result)
            except Empty:
                continue
            except Exception as e:
                logging.error("Exception in exsi client", exc_info=True)
                exsi_client.stop()
                response_queue.put(e)
                break

        exsi_client.stop()
        logging.debug("Exsi client stopped")

    except Exception as e:
        logging.error("Unhandled exception in run_exsi_client", exc_info=True)
    finally:
        pipe.close()
        logging.debug("Pipe closed")

def call_method(request_queue, response_queue, method, *args, **kwargs):
    request_queue.put((method, args, kwargs))
    result = response_queue.get()
    if isinstance(result, Exception):
        raise result
    return result

def read_stdout(pipe):
    try:
        while True:
            output = pipe.recv()
            if output == '':
                break
            print(output, end="")
    except EOFError:
        logging.debug("EOFError in read_stdout")
        pass

# Example use of this file
if __name__ == "__main__":
    process, request_queue, response_queue, parent_pipe, stop_event = start_exsi_client()

    time.sleep(5)

    stdout_thread = threading.Thread(target=read_stdout, args=(parent_pipe,))
    stdout_thread.daemon = True
    stdout_thread.start()

    try:
        result = call_method(request_queue, response_queue, 'sendLoadProtocol', 'BPT_EXSI')
        print("Result:", result)

    finally:
        stop_event.set()
        process.join()
        parent_pipe.close()