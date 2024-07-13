import queue
import re
import socket
import struct
import threading
from datetime import datetime

import numpy as np

from shimTool.utils import execSSHCommand


class exsi:
    def __init__(
        self,
        config,
        shimZeroFunc=None,
        shimCurrentFunc=None,
        debugging=False,
        output_file="scanner_log.txt",
    ):
        self.debugging = debugging

        self.host = config["host"]
        self.port = config["exsiPort"]
        self.exsiProduct = config["exsiProduct"]
        self.exsiPasswd = config["exsiPasswd"]
        self.hvPort = config["hvPort"]
        self.hvUser = config["hvUser"]
        self.hvPassword = config["hvPassword"]
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.s.settimeout(1)  # Set a timeout of 1 second

        # cond vars
        self.send_event = threading.Event()  # for every time command is sent to scanner
        self.ready_event = threading.Event()
        self.images_ready_event = threading.Event()
        self.connected_ready_event = threading.Event()
        self.no_failures = threading.Event()  # for when command fails.
        self.no_failures.set()  # set to true initially
        self.prescanDone = threading.Event()  # for when prescan is done

        # queues
        self.command_queue = queue.Queue()  # Command queue
        self.output_file = output_file
        self.last_command = ""
        # task queue, if a protocol is loaded so that we can run tasks in order
        # NOTE: "task" as in task numbers related to the individual scanned sequences in an Exam
        self.task_queue = queue.Queue()

        # state vars
        self.counter = 0
        self.running = False
        self.taskKeys = (
            None  # taskKeys starts out None, and is replaced when LoadProtocol is called with all the new taskKeys
        )
        self.defaultCoil = None
        self.examNumber = None
        self.ogCenterFrequency = None
        self.ogLinearGradients = None
        self.bedPosition = None
        self.patientName = None

        # function passed from GUI to directly queue a shimSetCurrentManual command
        if shimZeroFunc is not None and shimCurrentFunc is not None:
            self.sendCurrentCmd = lambda channel, current: shimCurrentFunc(channel, current)
            self.sendZeroCmd = lambda: shimZeroFunc()
            self.clearShimQueue = lambda: None

        # Clear the Log
        with open(self.output_file, "w"):
            pass

        self.connectExsi()

    def connectExsi(self):
        try:
            self.s.connect((self.host, self.port))
            print(f"INFO EXSI CLIENT: Socket connected")
            # these trigger the connected event!
            self.running = True
            self.start_receiving_thread()
            self.start_command_processor_thread()  # Start the command processor thread

            self.send(f"ConnectToScanner product={self.exsiProduct} passwd={self.exsiPasswd}")
            self.send("NotifyEvent all=on")
            self.send("GetExamInfo")  # TODO: use the correct get set methods at bottom.
        except Exception as e:
            print(
                "ERROR EXSI CLIENT: Connection refused. Please check the host and port.\n Error: ",
                e,
            )

    def start_command_processor_thread(self):
        def process_commands():
            while self.running:
                try:
                    # Wait for up to 1 second
                    cmd = self.command_queue.get(timeout=1)
                    print("EXSI CLIENT DEBUG: Processing command: ", cmd)
                    # check if we need to initialize current switch as well...
                    if cmd.startswith("X"):
                        # Process synced shim current set command. 
                        # shouldn't process anything else after for this cycle
                        pattern = r"X\s(\d+)\s(-?\d+\.\d+)"
                        match = re.match(pattern, cmd)
                        if match is not None:
                            channel = int(match.group(1))
                            current = float(match.group(2))
                            self.sendCurrentCmd(channel, current)
                            if self.debugging:
                                print(
                                    f"EXSI CLIENT Debug: SEND SYNC CURRENT COMMAND, channel {channel} current {current:.2f}"
                                )
                            continue
                        else:
                            print(f"EXSI CLIENT DEBUG: Did not register {cmd} as a valid current command.")
                            raise ValueError(f"Invalid current command: {cmd}")
                    if not "WaitImagesReady" in cmd:
                        # don't send a command if we are just using a dummy message to wait for images to be collected...
                        self._send_command(cmd)
                    else:
                        self.last_command = cmd
                    # TODO: see if this timeout of 60 can be fixed in any way here...
                    ready = self.ready_event.wait(60)
                    if not ready:
                        self.stop()
                        raise TimeoutError(f"Error: Command {cmd} was sent to scanner. Timeout waiting for valid recv.")
                    # Response was recieved, clear the event and pop cmd from queue
                    self.ready_event.clear()
                    self.command_queue.task_done()
                except queue.Empty:
                    # Go back to the start of the loop to check self.running again
                    continue

        self.command_processor_thread = threading.Thread(target=process_commands)
        self.command_processor_thread.daemon = True
        self.command_processor_thread.start()

    def start_receiving_thread(self):
        self.receiving_thread = threading.Thread(target=self.receive_loop)
        self.receiving_thread.daemon = True
        self.receiving_thread.start()

    def send(self, cmd, immediate=False):
        if immediate:
            # For immediate commands like the initial connection; bypass the queue
            self._send_command(cmd)
        else:
            # check if we are trying to queue the next task without
            # a task number specified; i.e. it looks like 'taskkey='
            if re.search("SelectTask taskkey=(?!\d+)", cmd) is not None:
                # pull the next task from the task list and append
                tasktodo = str(self.task_queue.get())
                self.task_queue.task_done()
                self.command_queue.put(cmd + tasktodo)
                return
            elif "scan" in cmd:
                self.images_ready_event.clear()
            # add command directly to queue if nothing is special
            self.command_queue.put(cmd)
            self.send_event.clear()

    def _send_command(self, cmd):
        if cmd is not None:
            self.ready_event.clear()
            self.last_command = cmd
            tcmd = b">heartvista:1:" + str(self.counter).encode("utf-8") + b">" + cmd.encode("utf-8")
            self.counter += 1
            self.s.send(struct.pack("!H", 16))
            self.s.send(struct.pack("!H", 1000))
            self.s.send(struct.pack("!I", len(tcmd)))
            self.s.send(struct.pack("!I", 0))
            self.s.send(struct.pack("!I", 100))
            self.s.send(tcmd)
            self.send_event.set()

    def receive_loop(self):
        while self.running:
            try:
                msg = self.rcv()
                if msg:
                    success, is_ready, images_ready = self.is_ready(msg)
                    with open(self.output_file, "a") as file:
                        current_time = datetime.now()
                        # Format the current time as a string (e.g., HH:MM:SS)
                        formatted_time = current_time.strftime("%H:%M:%S")
                        file.write(f"{formatted_time} Received: " + msg + "\n")
                    if not success:
                        notify = "Command Failed: "
                        notify += self.last_command
                        notify += "\nClearing Command Queue\n\n"
                        with open(self.output_file, "a") as file:
                            file.write(notify)
                        print(f"EXSI CLIENT DEBUG: Command {self.last_command} failed, clearing command queue.")
                        self.clear_command_queue()  # Clear the queue on failure
                        self.clearShimQueue()  # Clear the shim queue too on failure

                        # signal that error occured. only cleared here, set by gui when ready to go again
                        self.no_failures.clear()
                        # set these so that gui can resume control and not sit in wait
                        self.ready_event.set()
                        self.images_ready_event.set()
                    if is_ready:
                        self.ready_event.set()
                    if images_ready:
                        print(f"EXSI CLIENT DEBUG: setting images_ready_event")
                        self.images_ready_event.set()
            except socket.timeout:
                continue
            except Exception as e:
                with open(self.output_file, "a") as file:
                    file.write("Error receiving data: " + str(e) + "\n!!!Please Restart the Client!!!\n")
                break

    def rcv(self, length=6000):
        data = self.s.recv(length)
        msg = str(data, "UTF-8", errors="ignore")
        msg = msg[msg.find("<") + 1 :]
        return msg

    def is_ready(self, msg):
        # Return a tuple (success, is_ready, images_ready)

        success = True  # Assume success unless a failure condition is detected
        ready = False
        images_ready = False

        if "images available" in msg:
            print(f"EXSI CLIENT DEBUG: Images are ready. in msg: {msg}")
            images_ready = True

        # TODO: this is kind of a confuzzling place to put this contradiction
        if "fail" in msg:
            success = False  # Command failed
            ready = True  # ready for next command bc we clear the queue

        # Check for specific command completion or readiness indicators
        elif self.last_command.startswith("ConnectToScanner"):
            ready = "ConnectToScanner=ok" in msg
        elif self.last_command.startswith("Scan"):
            ready = "acquisition=complete" in msg
        elif self.last_command.startswith("WaitImagesReady"):
            ready = images_ready
        elif self.last_command.startswith("ActivateTask"):
            ready = "ActivateTask=ok" in msg
        elif self.last_command.startswith("SelectTask"):
            ready = "SelectTask=ok" in msg
        elif self.last_command.startswith("PatientTable"):
            ready = "PatientTable=ok" in msg
        elif self.last_command.startswith("LoadProtocol"):
            ready = "LoadProtocol=ok" in msg
            # i want to use regex to extract out task keys from message.
            pattern = r"taskKeys=([0-9, ]+)"
            match = re.search(pattern, msg)
            if match:
                taskKeys = match.group(1).split(",")
                taskKeys = [int(key.strip()) for key in taskKeys]
                print(f"EXSI CLIENT DEBUG: Task keys found in message: ", taskKeys)
                self.taskKeys = taskKeys
                for task in taskKeys:
                    self.task_queue.put(task)
            # else:
            #     print(f"EXSI CLIENT DEBUG: No task keys found in message: {msg}")
        elif self.last_command.startswith("SetCVs"):
            ready = "SetCVs=ok" in msg
        elif self.last_command.startswith("Prescan"):
            if "auto" in self.last_command:
                if "scanner=idle" in msg:
                    ready = True
                    self.prescanDone.set()
            elif "skip" in self.last_command:
                ready = "Prescan=ok" in msg
            elif "values=hide" in self.last_command:  # for setting the center frequency
                ready = "Prescan=ok" in msg
        elif self.last_command.startswith("GetExamInfo"):
            if "GetExamInfo=ok" in msg:
                ref = "0020,0010="
                examnumstart = msg.find(ref) + len(ref)
                self.examNumber = msg[examnumstart : examnumstart + 5]
                ref = "0010,0010="
                patientnamestart = msg.find(ref) + len(ref)
                patientnameend = msg.find(" ", patientnamestart)
                self.patientName = msg[patientnamestart:patientnameend]
                ready = True
                self.connected_ready_event.set()  # This only needs to happen once
        elif self.last_command.startswith("GetPrescanValues"):
            if "GetPrescanValues=ok" in msg:
                pattern = r"cf=(\d+)"
                match = re.search(pattern, msg)
                if match:
                    self.ogCenterFrequency = match.group(1)
                    print(f"EXSI CLIENT DEBUG: Center frequency found in message: {self.ogCenterFrequency}")
                else:
                    print("EXSI CLIENT DEBUG: Center frequency not found in message.")
                self.getLastSetGradients()
                ready = True
        elif self.last_command.startswith("SetGrxSlices"):
            if "SetGrxSlices=ok" in msg:
                ready = True
        elif self.last_command.startswith("SetRxGeometry"):
            if "SetRxGeometry=ok" in msg:
                ready = True
        elif self.last_command.startswith("SetRxParams"):
            if "SetRxParams=ok" in msg:
                ready = True
        elif self.last_command.startswith("SetShimValues"):
            ready = "SetShimValues=ok" in msg
        elif self.last_command.startswith("Help"):
            ready = "Help" in msg

        else:
            # Default condition if none of the above matches
            ready = "NotifyEvent" in msg  # Default readiness condition

        return (success, ready, images_ready)

    def clear_command_queue(self):
        while not self.command_queue.empty():
            try:
                cmd = self.command_queue.get_nowait()
                print(f"EXSI CLIENT DEBUG: Clearing command: {cmd}")
                self.command_queue.task_done()
            except queue.Empty:
                break
        with open(self.output_file, "a") as file:
            file.write("Command queue cleared due to failure.\n")

    def stop(self):
        self.running = False
        # print out command queue if it is not empty
        self.clear_command_queue()
        if self.running:
            self.s.shutdown(socket.SHUT_RDWR)
            self.s.close()
            print("INFO EXSI CLIENT: socket closed successfully. bye.")

    def getLastSetGradients(self):
        # Command to extract the last successful setting of the shim currents
        print(f"EXSI CLIENT DEBUG: attempting to find the last used gradients")
        command = "tail -n 100 /usr/g/service/log/Gradient.log | grep 'Prescn Success: AS Success' | tail -n 1"
        output = execSSHCommand(self.host, self.hvPort, self.hvUser, self.hvPassword, command)
        if output:
            last_line = output[0].strip()
            # Use regex to find X, Y, Z values
            match = re.search(r"X =\s+(-?\d+)\s+Y =\s+(-?\d+)\s+Z =\s+(-?\d+)", last_line)
            if match:
                gradients = [int(match.group(i)) for i in range(1, 4)]
                print(f"EXSI CLIENT DEBUG: Debug: found that linear shims got set to {gradients}")
                self.ogLinearGradients = np.array(gradients, dtype=np.float64)
            else:
                print(f"EXSI CLIENT DEBUG: no matches!")
        else:
            print(f"Debug: failed to find the last used gradients")
        return None
    
    def getLastSetBedPosition(self):
        # function to extract the last bed position from the scanner
        file = "/usr/g/service/log/irmJvm.log"
        command = f"tail -n 500 {file} | grep 'Table Position=' | tail -n 1"
        output = execSSHCommand(self.host, self.hvPort, self.hvUser, self.hvPassword, command)
        if output: 
            last_line = output[0].strip()
            match = re.search(r"Table Position=((S|I)\d+)", last_line)
            if match:
                sign = 1 if match.group(2) == "S" else -1
                position = int(match.group(1)[1:]) * sign
                print(f"EXSI CLIENT DEBUG: found that the last bed position was {position} mm")
                self.bedPosition = position
            else:
                print("EXSI CLIENT DEBUG: no matches for bed position.")
        else:
            print(f"EXSI CLIENT DEBUG: failed to find the last bed position in {file}.")


    ##### EXSI CLIENT CONTROL FUNCTIONS #####

    def requireExsiConnected(func):
        """Decorator to check if the EXSI client is connected before running a function."""

        def wrapper(self, *args, **kwargs):
            # Check the status of the event
            if not self.connected_ready_event.is_set() and not self.debugging:
                # Show a message to the user, reconnect shim client.
                raise ExsiError("ExSI Client Not Connected.")
            return func(self, *args)

        return wrapper

    @requireExsiConnected
    def sendLoadProtocol(self, name):
        self.send('LoadProtocol site path="' + name + '"')

    @requireExsiConnected
    def sendSelTask(self):
        self.send("SelectTask taskkey=")

    @requireExsiConnected
    def sendActTask(self):
        self.send("ActivateTask")

    @requireExsiConnected
    def sendPatientTable(self):
        self.send("PatientTable advanceToScan")

    @requireExsiConnected
    def sendScan(self):
        self.send("Scan")

    @requireExsiConnected
    def sendGetExamInfo(self):
        self.send("GetExamInfo")

    @requireExsiConnected
    def sendSetCV(self, name, value):
        self.send(f"SetCVs {name}={value}")

    @requireExsiConnected
    def sendPrescan(self, auto=False):
        if auto:
            self.send("Prescan auto")  # tune the transmit gain and such
        else:
            self.send("Prescan skip")

    @requireExsiConnected
    def sendSetCenterFrequency(self, freq: int):
        self.send(f"Prescan values=hide cf={freq}")

    @requireExsiConnected
    def sendSetShimValues(self, x: int, y: int, z: int):
        self.send(f"SetShimValues x={x} y={y} z={z}")

    @requireExsiConnected
    def sendWaitForImagesCollected(self):
        self.send(f"WaitImagesReady")

    @requireExsiConnected
    def sendSetScanPlaneOrientation(self, plane:str=None):
        """plane: str, one of 'coronal', 'sagittal', 'axial'"""
        if plane is None:
            plane = "coronal"
        self.send(f"SetRxGeometry plane={plane}")

    @requireExsiConnected
    def sendSetCenterPosition(self, plane=None, center=None):
        """
        center: list[float], [r/l,a/p,s/i]
        plane: str, one of 'coronal', 'sagittal', 'axial'

        if they are not provided, it defaults to the last bed position in S/I direction and zero otherwise
        the plane defaults to coronal
        """
        def helper(triple):
            return f"{triple[0]},{triple[1]},{triple[2]}"

        if plane is None:
            plane = "coronal"
        if center is None:
            if self.bedPosition is None:
                self.getLastSetBedPosition()
            center = [0.0,0.0,self.bedPosition]

        if plane=="coronal":
            phaseNormal = [10,0,0]
            freqNormal = [0,0,10]
        elif plane=="sagittal":
            phaseNormal = [0,10,0]
            freqNormal = [0,0,10]
        else: # axial
            phaseNormal = [10,0,0]
            freqNormal = [0,10,0]

        self.send(f"SetGrxSlices center={helper(center)} phaseNormal={helper(phaseNormal)} freqNormal={helper(freqNormal)}")
    
    @requireExsiConnected
    def sendSetCoil(self, coil:str=None):
        if coil == None:
            if self.defaultCoil is None:
                return
            coil = self.defaultCoil
        self.send(f"SetRxParams coil={coil}")

    def __del__(self):
        self.stop()


class ExsiError(Exception):
    """Base class for exceptions in this module."""

    pass
