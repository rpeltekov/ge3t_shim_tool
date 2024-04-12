import socket
import struct
import threading
import queue
import re
from datetime import datetime


class exsi:
    def __init__(self, host, port, exsiProduct, exsiPasswd, shimZeroFunc, shimCurrentFunc, output_file='scanner_log.txt', debugging=False):
        self.debugging = debugging
    
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.s.settimeout(1)  # Set a timeout of 1 second
        self.s.connect((host, port))
        print(f"INFO EXSI CLIENT: Socket connected successfully")
        self.counter = 0
        self.running = True
        self.ready_event = threading.Event()
        self.images_ready_event = threading.Event()
        self.command_queue = queue.Queue()  # Command queue
        self.output_file = output_file
        self.last_command = ""
        self.examNumber = None
        # task queue, if a protocol is loaded so that we can run tasks in order
        # NOTE: "task" as in task numbers related to the individual scanned sequences in an Exam
        self.task_queue = queue.Queue() 

        # function passed from GUI to directly queue a shimSetCurrentManual command
        self.sendCurrentCmd = lambda channel, current: shimCurrentFunc(channel, current)
        self.sendZeroCmd = lambda : shimZeroFunc()

        # Clear the Log
        with open(self.output_file, 'w'):
            pass

        self.start_receiving_thread()
        self.start_command_processor_thread()  # Start the command processor thread
        self.send(f'ConnectToScanner product={exsiProduct} passwd={exsiPasswd}')
        self.send('NotifyEvent all=on')
        self.send('GetExamInfo')

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

    def rcv(self, length=6000):
        data = self.s.recv(length)
        msg = str(data, 'UTF-8', errors='ignore')
        msg = msg[msg.find('<')+1:]
        return msg

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
                        self.clear_command_queue()  # Clear the queue on failure
                    if is_ready:
                        self.ready_event.set()
                    if images_ready:
                        self.images_ready_event.set()
            except socket.timeout:
                continue
            except Exception as e:
                with open(self.output_file, 'a') as file:
                    file.write("Error receiving data: " + str(e) + \
                               "\n!!!Please Restart the Client!!!\n")
                break

    def is_ready(self, msg):
        # Return a tuple (success, is_ready, images_ready)
        
        success = True  # Assume success unless a failure condition is detected
        ready = False 
        images_ready = False

        # TODO(rob): this is kind of a confuzzling place to put this contradiction
        if "fail" in msg:
            success = False # Command failed
            ready = True # ready for next command bc we clear the queue
            
        # Check for specific command completion or readiness indicators
        elif self.last_command.startswith("ConnectToScanner"):
            ready = "ConnectToScanner=ok" in msg
        elif self.last_command.startswith("Scan"):
            ready = "acquisition=complete" in msg
            if "images available" in msg:
                images_ready = True
        elif self.last_command.startswith("ActivateTask"):
            ready = "ActivateTask=ok" in msg
        elif self.last_command.startswith("SelectTask"):
            ready = "SelectTask=ok" in msg
        elif self.last_command.startswith("PatientTable"):
            ready = "PatientTable=ok" in msg
        elif self.last_command.startswith("LoadProtocol"):
            ready = "LoadProtocol=ok" in msg
            tasks = msg[msg.find('taskKeys=')+9:-2].split(",")
            for task in tasks:
                self.task_queue.put(task)
        elif self.last_command.startswith("SetCVs"):
            ready = "SetCVs=ok" in msg
        elif self.last_command.startswith("Prescan auto"):
            ready = "scanner=idle" in msg
        elif self.last_command.startswith("Prescan skip"):
            ready = "Prescan=ok" in msg
        elif self.last_command.startswith("GetExamInfo"):
            ref = "0020,0010="
            examnumstart = msg.find(ref) + len(ref)
            self.examNumber = msg[examnumstart:examnumstart+5]
            ready = True
        elif self.last_command.startswith("SetGrxSlices"):
            ready = True
        elif self.last_command.startswith("SetGrxsomething...."):
            ready = True
        elif self.last_command.startswith("Help"):
            ready = "Help" in msg
            
        else:
            # Default condition if none of the above matches
            ready = "NotifyEvent" in msg        # Default readiness condition

        return (success, ready, images_ready)

    def clear_command_queue(self):
        while not self.command_queue.empty():
            try:
                self.command_queue.get_nowait()
                self.command_queue.task_done()
            except queue.Empty:
                break
        with open(self.output_file, 'a') as file:
            file.write("Command queue cleared due to failure.\n")

    def start_receiving_thread(self):
        self.receiving_thread = threading.Thread(target=self.receive_loop)
        self.receiving_thread.daemon = True
        self.receiving_thread.start()

    def start_command_processor_thread(self):
        def process_commands():
            while self.running:
                try:
                    # Wait for up to 1 second
                    cmd = self.command_queue.get(timeout=1) 
                    # check if we need to initialize current switch as well...
                    if "|" in cmd:
                        cmd = cmd.split(" | ")
                        cmd, current_cmd = cmd[0], cmd[1]
                        pattern = r"(\d+)\s(\d+\.\d+)"
                        match = re.match(pattern, current_cmd)
                        channel = int(match.group(1))
                        current = float(match.group(2))
                        if self.debugging:
                            print(f"EXSI CLIENT Debug: SENDING CURRENT COMMANDS, channel {channel} current {current:.2f}")
                        self.sendZeroCmd()
                        self.sendCurrentCmd(channel, current)
                    self._send_command(cmd)
                    #TODO(rob): see if this timeout of 60 can be fixed in any way here...
                    ready = self.ready_event.wait(60)
                    if not ready:
                        self.stop()
                        raise TimeoutError("Error: Command was sent to scanner. Timeout waiting for valid recv.")
                    # Response was recieved, clear the event and pop cmd from queue
                    self.ready_event.clear()
                    self.command_queue.task_done()
                except queue.Empty:
                    # Go back to the start of the loop to check self.running again
                    continue

        self.command_processor_thread = threading.Thread(target=process_commands)
        self.command_processor_thread.daemon = True
        self.command_processor_thread.start()

    def stop(self):
        self.running = False
        self.s.shutdown(socket.SHUT_RDWR)
        self.s.close()
        print("INFO EXSI CLIENT: socket closed successfully. bye.")
    
    def __del__(self):
        self.stop()
