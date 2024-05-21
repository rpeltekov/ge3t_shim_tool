import socket
import struct
import threading
import queue
import re
from datetime import datetime


class exsi:
    def __init__(self, host, port, exsiProduct, exsiPasswd, shimZeroFunc, shimCurrentFunc, output_file='scanner_log.txt', debugging=False):
        self.debugging = debugging
    
        self.host = host
        self.port = port
        self.exsiProduct = exsiProduct
        self.exsiPasswd = exsiPasswd
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.s.settimeout(1)  # Set a timeout of 1 second

        self.counter = 0
        self.running = False
        self.ready_event = threading.Event()
        self.images_ready_event = threading.Event()
        self.connected_ready_event = threading.Event()
        self.no_failures = threading.Event() # for when command fails. 
        self.no_failures.set() # set to true initially
        self.command_queue = queue.Queue()  # Command queue
        self.output_file = output_file
        self.last_command = ""
        self.examNumber = None
        self.ogCenterFreq = None
        self.newCenterFreq = None
        self.patientName = None

        # task queue, if a protocol is loaded so that we can run tasks in order
        # NOTE: "task" as in task numbers related to the individual scanned sequences in an Exam
        self.task_queue = queue.Queue() 

        # function passed from GUI to directly queue a shimSetCurrentManual command
        self.sendCurrentCmd = lambda channel, current: shimCurrentFunc(channel, current)
        self.sendZeroCmd = lambda : shimZeroFunc()
        self.clearShimQueue = lambda : None

        # Clear the Log
        with open(self.output_file, 'w'):
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

            self.send(f'ConnectToScanner product={self.exsiProduct} passwd={self.exsiPasswd}')
            self.send('NotifyEvent all=on')
            self.send('GetExamInfo') #TODO: use the correct get set methods at bottom.
        except Exception as e:
            print("ERROR EXSI CLIENT: Connection refused. Please check the host and port.\n Error: ", e)

    def start_command_processor_thread(self):
        def process_commands():
            while self.running:
                try:
                    # Wait for up to 1 second
                    cmd = self.command_queue.get(timeout=1) 
                    print("EXSI CLIENT DEBUG: Processing command: ", cmd)
                    # check if we need to initialize current switch as well...
                    pattern = r"(\d+)\s(\d+\.\d+)"
                    if "|" in cmd:
                        # Proess synced shim current set command for calibration (Zero first, and also load protocol)
                        cmd = cmd.split(" | ")
                        cmd, current_cmd = cmd[0], cmd[1]
                        match = re.match(pattern, current_cmd)
                        channel = int(match.group(1))
                        current = float(match.group(2))
                        if self.debugging:
                            print(f"EXSI CLIENT Debug: SEND CURRENT COMMAND, channel {channel} current {current:.2f}")
                        self.sendZeroCmd()
                        self.sendCurrentCmd(channel, current)
                    if cmd.startswith("X"):
                        # Proess synced shim current set command. shouldn't queue anything else after this
                        match = re.match(pattern, cmd)
                        channel = int(match.group(1))
                        current = float(match.group(2))
                        self.sendCurrentCmd(channel, current)
                        if self.debugging:
                            print(f"EXSI CLIENT Debug: SEND SYNC CURRENT COMMAND, channel {channel} current {current:.2f}")
                        continue
                    if not "WaitImagesReady" in cmd:
                        # don't send a command if we are just using a dummy message to wait for images to be collected...
                        self._send_command(cmd)
                    else:
                        self.last_command = cmd
                    #TODO: see if this timeout of 60 can be fixed in any way here...
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
            elif 'scan' in cmd:
                self.images_ready_event.clear()
            # add command directly to queue if nothing is special
            self.command_queue.put(cmd)

    def _send_command(self, cmd):     
        if cmd is not None:
            self.ready_event.clear()
            self.last_command = cmd
            tcmd = b'>heartvista:1:' + str(self.counter).encode('utf-8') + b'>' + cmd.encode('utf-8')
            self.counter += 1
            self.s.send(struct.pack('!H', 16))
            self.s.send(struct.pack('!H', 1000))
            self.s.send(struct.pack('!I', len(tcmd)))
            self.s.send(struct.pack('!I', 0))
            self.s.send(struct.pack('!I', 100))
            self.s.send(tcmd)

    def receive_loop(self):
        while self.running:
            try:
                msg = self.rcv()
                if msg:
                    success, is_ready, images_ready = self.is_ready(msg)
                    with open(self.output_file, 'a') as file:
                        current_time = datetime.now()
                        # Format the current time as a string (e.g., HH:MM:SS)
                        formatted_time = current_time.strftime('%H:%M:%S')
                        file.write(f"{formatted_time} Received: " + msg + "\n")
                    if not success:
                        notify = "Command Failed: "
                        notify += self.last_command
                        notify += "\nClearing Command Queue\n\n"
                        with open(self.output_file, 'a') as file:
                            file.write(notify)
                        print(f"EXSI CLIENT DEBUG: Command {self.last_command} failed, clearing command queue.")
                        self.clear_command_queue()  # Clear the queue on failure
                        self.clearShimQueue() # Clear the shim queue too on failure

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
                with open(self.output_file, 'a') as file:
                    file.write("Error receiving data: " + str(e) + \
                               "\n!!!Please Restart the Client!!!\n")
                break

    def rcv(self, length=6000):
        data = self.s.recv(length)
        msg = str(data, 'UTF-8', errors='ignore')
        msg = msg[msg.find('<')+1:]
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
            success = False # Command failed
            ready = True # ready for next command bc we clear the queue
            
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
                taskKeys = match.group(1).split(',')
                taskKeys = [int(key.strip()) for key in taskKeys]
                print(f"EXSI CLIENT DEBUG: Task keys found in message: ", taskKeys)
                for task in taskKeys:
                    self.task_queue.put(task)
            # else:
            #     print(f"EXSI CLIENT DEBUG: No task keys found in message: {msg}")
        elif self.last_command.startswith("SetCVs"):
            ready = "SetCVs=ok" in msg
        elif self.last_command.startswith("Prescan"):
            if "auto" in self.last_command:
                ready = "scanner=idle" in msg
            elif "skip" in self.last_command:
                ready = "Prescan=ok" in msg
            elif "values=hide" in self.last_command: # for setting the center frequency
                ready = "Prescan=ok" in msg
                # extract the new center frequency from the message
                pattern = r"cf=(\d+)"
                match = re.search(pattern, msg)
                if match:
                    self.newCenterFreq = match.group(1)
        elif self.last_command.startswith("GetExamInfo"):
            if "GetExamInfo=ok" in msg:
                ref = "0020,0010="
                examnumstart = msg.find(ref) + len(ref)
                self.examNumber = msg[examnumstart:examnumstart+5]
                ref = "0010,0010="
                patientnamestart = msg.find(ref) + len(ref)
                patientnameend = msg.find(" ", patientnamestart)
                self.patientName = msg[patientnamestart:patientnameend]
                ready = True
                self.connected_ready_event.set() # This only needs to happen once
        elif self.last_command.startswith("GetPrescanValues"):
            if "GetPrescanValues=ok" in msg:
                pattern = r"cf=(\d+)"
                match = re.search(pattern, msg)
                if match:
                    self.ogCenterFreq = match.group(1)
                    print(f"EXSI CLIENT DEBUG: Center frequency found in message: {self.ogCenterFreq}")
                else:
                    print("EXSI CLIENT DEBUG: Center frequency not found in message.")
                ready = True
        elif self.last_command.startswith("SetGrxSlices"):
            ready = True
        elif self.last_command.startswith("SetRxsomething...."): #TODO: what is this command?
            ready = True
        elif self.last_command.startswith("SetShimValues"):
            ready = "SetShimValues=ok" in msg
        elif self.last_command.startswith("Help"):
            ready = "Help" in msg
            
        else:
            # Default condition if none of the above matches
            ready = "NotifyEvent" in msg        # Default readiness condition

        return (success, ready, images_ready)

    def clear_command_queue(self):
        while not self.command_queue.empty():
            try:
                cmd = self.command_queue.get_nowait()
                print(f"EXSI CLIENT DEBUG: Clearing command: {cmd}")
                self.command_queue.task_done()
            except queue.Empty:
                break
        with open(self.output_file, 'a') as file:
            file.write("Command queue cleared due to failure.\n")

    def stop(self):
        self.running = False
        # print out command queue if it is not empty
        self.clear_command_queue()
        if self.running:
            self.s.shutdown(socket.SHUT_RDWR)
            self.s.close()
            print("INFO EXSI CLIENT: socket closed successfully. bye.")
        
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
        self.send('SelectTask taskkey=')

    @requireExsiConnected
    def sendActTask(self):
        self.send('ActivateTask')

    @requireExsiConnected
    def sendPatientTable(self):
        self.send('PatientTable advanceToScan')

    @requireExsiConnected
    def sendScan(self):
        self.send('Scan')

    @requireExsiConnected
    def sendGetExamInfo(self):
        self.send('GetExamInfo')
    
    @requireExsiConnected
    def sendSetCV(self, name, value):
        self.send(f"SetCVs {name}={value}")

    @requireExsiConnected
    def sendPrescan(self, auto=False):
        if auto:
            self.send("Prescan auto") # tune the transmit gain and such
        else:
            self.send("Prescan skip")

    @requireExsiConnected
    def sendSetCenterFrequency(self, freq:int):
        self.send(f"Prescan values=hide cf={freq}")
    
    @requireExsiConnected
    def sendSetShimValues(self, x:int, y:int, z:int):
        self.send(f"SetShimValues x={x} y={y} z={z}")

    @requireExsiConnected
    def sendWaitForImagesCollected(self):
        self.send(f"WaitImagesReady")

    
    def __del__(self):
        self.stop()

class ExsiError(Exception):
    """Base class for exceptions in this module."""
    pass