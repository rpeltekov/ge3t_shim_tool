import serial
import threading
import queue
import re
from datetime import datetime

class shim:
    def __init__(self, port, baudRate, outputFile, defaultTimeout=1, debugging=False):
        self.debugging = debugging
        self.port = port
        self.baudRate = baudRate
        self.outputFile = outputFile
        self.defaultTimeout = defaultTimeout

        self.readyEvent = threading.Event()
        self.connectedEvent = threading.Event()
        self.commandQueue = queue.Queue()
        self.ser = None
        self.readThread = None
        self.running = None
        self.lastCommand = ""

        # TODO(rob): add a way to set the num loops and update the arduino code to accept those changes
        self.numLoops = 2
        self.loopCurrents = [0 for _ in range(self.numLoops)] 
        self.calibrated = False

        # Clear the Log
        with open(self.outputFile, "w"):
            pass

        # Init the connection
        try:
            self.openPort()
        except Exception as e:
            print(f"ERROR SHIM CLIENT: Please restart the client or fix bug: {e}")

        # Start Daemon Threads
        self.startRecieveThread()
        self.startCommandProcessThread()

    def openPort(self):
        try:
            self.ser = serial.Serial(self.port, self.baudRate, timeout=self.defaultTimeout)
            self.running = True
            self.connectedEvent.set()
            print(f"INFO SHIM CLIENT: Serial port opened successfully")
        except serial.SerialException as e:
            print(f"Debug: Failed to open serial port: {e}")

    def startRecieveThread(self):
        if self.ser and self.ser.is_open:
            self.readThread = threading.Thread(target=self.readLoop)
            self.readThread.daemon = True
            self.readThread.start()
        else:
            print(f"Debug SHIM CLIENT: Failed to start reading thread. Serial port is not open")

    def startCommandProcessThread(self):
        def processCommands():
            while self.running:
                try:
                    # Wait for up to 1 second
                    cmd = self.commandQueue.get(timeout=1) 
                    self._sendCommand(cmd)
                    # the arduino should be able to send a response immediately
                    ready = self.readyEvent.wait(1)
                    if not ready:
                        self.stop()
                        raise TimeoutError("Error: Command send to Shim Arduino. No valid responce recv.")
                    # Response was recieved, clear the event and pop cmd from queue
                    self.readyEvent.clear()
                    self.commandQueue.task_done()
                except queue.Empty:
                    # Go back to the start of the loop to check self.running again
                    continue

        self.command_processor_thread = threading.Thread(target=processCommands)
        self.command_processor_thread.daemon = True
        self.command_processor_thread.start()

    def readLoop(self):
        try:
            while self.running:
                if self.ser.inWaiting() > 0:
                    msg = self.ser.readline().decode('utf-8').rstrip()
                    if self.debugging:
                        print(f"Debug SHIM CLIENT: recieved msg: {msg}")
                    
                    # Append the message that was recieved to the log
                    with open(self.outputFile, 'a') as file:
                        current_time = datetime.now()
                        # Format the current time as a string (e.g., HH:MM:SS)
                        formatted_time = current_time.strftime('%H:%M:%S')
                        file.write(f"{formatted_time} Received: " + msg + "\n")

                    ready, fail = self.processLine(msg)

                    # if response indicates self.lastCommand successfully completed,
                    # free the command Process thread to issue next command
                    if ready:
                        self.readyEvent.set()
                    # if failure condition met, clear the command queue, and ready for more
                    if fail:
                        notify = "Command Failed: "
                        notify += self.lastCommand
                        notify += "\nClearing Command Queue\n\n"
                        with open(self.outputFile, 'a') as file:
                            file.write(notify)
                        self.clearCommandQueue()  # Clear the queue on failure
        except Exception as e:
            print(f"Debug SHIM CLIENT: Error while reading from serial port: {e}")

    def processLine(self, msg):
        ready = False
        fail = False

        if self.lastCommand.startswith("I"):
            fail = "X" in msg
            ready = "Done Printing Currents" in msg
            # TODO(rob): update the loop currents here too whenever this is run.
        elif self.lastCommand.startswith("C"):
            fail = "failed (cal)" in msg
            ready = "Done Calibrating" in msg
            if ready:
                self.calibrated = True
        elif self.lastCommand.startswith("X"):
            fail = False

            if "Done Setting Current" in msg:
                ready = True

            # update our record of loop current and verify it got ingested as expected
            rec_pattern = r"board:\s(\d+);\schannel:\s(\d+);\svalue:\s(\d+\.\d+)"
            sent_pattern = r"X\s(\d+)\s(\d+)\s(\d+\.\d+)"
            match = re.search(rec_pattern, msg)
            if match:
                board = int(match.group(1))
                channel = int(match.group(2))
                current = float(match.group(3))
                
                expected = re.search(sent_pattern, self.lastCommand)
                expBoard = int(expected.group(1))
                expChannel = int(expected.group(2))
                expCurrent = float(expected.group(3))
                if (expBoard != board) or \
                    (expChannel != channel) or \
                    (f"{current:.2f}" != f"{expCurrent:.2f}"):
                    fail = True
                    print(f"Debug: Failed Command Mismatch in current setting:")
                    print(f"\tExpected:\t{expBoard}, {expChannel}, {expCurrent:.2f}")
                    print(f"\tGot:\t{board}, {channel}, {current:.2f}")
                else:
                    # TODO(rob): maybe add some error bounds checking for indexing this guy
                    # maybe some helper method or smth. get and set methods
                    self.loopCurrents[board*8+channel] = current
        elif self.lastCommand.startswith("Z"):
            ready = "Done Zeroing" in msg
            # TODO(rob): doesnt detect failure. think about it... maybe not it is so trivial
            fail = False
        else:
            ready = len(msg) > 0

        # TODO(rob): should probably think of some better way to signal this
        if fail:
            return fail, fail

        return ready, fail

    def send(self, cmd, immediate=False):
        if immediate:
            # For immediate commands, that maybe are good to launch on init
            self._sendCommand(cmd)
        else:
            # Else, queue up the command so they can be sent in order.
            self.commandQueue.put(cmd)

    def _sendCommand(self, cmd):
        if cmd is not None:
            self.lastCommand = cmd
            self.readyEvent.clear()
            self.ser.write(cmd.encode())

    def clearCommandQueue(self):
        while not self.commandQueue.empty():
            try:
                self.commandQueue.get_nowait()
                self.commandQueue.task_done()
            except queue.Empty:
                break
        with open(self.outputFile, 'a') as file:
            file.write("\n CMD Queue CLEARED due to failure.")
        
    def stop(self):
        # try to zero the loops so that current doesn't stay running through the system
        if self.connectedEvent.is_set():
            self._sendCommand("Z")
        self.running = False
        if self.ser:
            self.ser.close()
            print(f"INFO SHIM CLIENT: Closed connection to arduino. bye.")
    
    def __del__(self):
        self.stop()
    

#### OLD STUFFFFF
# def check_for_commands(command_file):
#     """Check the command file for new commands, execute them, and clear the file."""
#     try:
#         with open(command_file, "r+") as file:
#             commands = file.readlines()
#             file.seek(0)
#             file.truncate()  # Clear the file after reading commands
#         return commands
#     except FileNotFoundError:
#         return []

# def handle_serial(port, baud_rate=9600, command_file="commands.txt", log_file="arduino_log.txt"):
#     with open(command_file, "w") as file:
#         pass
#     with open(log_file, "w") as file:
#         pass

#     with serial.Serial(port, baud_rate, timeout=1) as ser:
#         print(f"Serial handler started for {port} at {baud_rate} baud.")
#         while True:
#             # Check for new commands
#             commands = check_for_commands(command_file)
#             for command in commands:
#                 ser.write(command.encode())
#                 time.sleep(0.5)  # Adjust based on your Arduino's needs
            
#             # Read and log data from Arduino
#             if ser.inWaiting() > 0:
#                 data = ser.readline().decode('utf-8').rstrip()
#                 print(data)  # Optional: Print to console
#                 with open(log_file, "a") as file:
#                     file.write(data + "\n")

if __name__ == "__main__":
    arduino_port = "/dev/ttyACM1"  # Adjust to your Arduino's serial port
    # handle_serial(arduino_port)
