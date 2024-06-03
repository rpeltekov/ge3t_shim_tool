import serial
import threading
import queue
import re
from datetime import datetime

from shimTool.utils import launchInThread

class shim:
    def __init__(self, config, outputFile, defaultTimeout=1, debugging=False):
        self.debugging = debugging
        self.port = config['shimPort']
        self.baudRate = config['shimBaudRate']
        self.outputFile = outputFile
        self.defaultTimeout = defaultTimeout

        self.readyEvent = threading.Event()
        self.connectedEvent = threading.Event()
        self.commandQueue = queue.Queue()
        self.ser = None
        self.readThread = None
        self.running = None
        self.lastCommand = ""

        # TODO: add a way to set the num loops and update the arduino code to accept those changes
        self.numLoops = 0
        self.loopCurrents = [0 for _ in range(self.numLoops)] 
        self.calibrated = False

        # this gets set in the Exsi Gui
        self.clearExsiQueue = lambda : None

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

        # startup procedure to show when connected
        def waitForConnection():
            self.send("C")
            if not self.readyEvent.is_set():
                self.readyEvent.wait()
            self.connectedEvent.set()
            print(f"INFO SHIM CLIENT: Connection Created successfully")
        t = threading.Thread(target=waitForConnection)
        t.daemon = True 
        t.start()

    def openPort(self):
        try:
            self.ser = serial.Serial(self.port, self.baudRate, timeout=self.defaultTimeout)
            print(f"INFO SHIM CLIENT: Serial port opened successfully")
            self.running = True
        except serial.SerialException as e:
            print(f"Debug: Failed to open serial port: {e}")
            # TODO(rob): maybe add a way to retry the connection

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
                        raise TimeoutError(f"Error: Command {cmd} send to Shim Arduino. No valid responce recv.")
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
                        self.clearExsiQueue()
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
                cmd = self.commandQueue.get_nowait()
                print(f"SHIM CLIENT Debug: Clearing command: {cmd}")
                self.commandQueue.task_done()
            except queue.Empty:
                break
        with open(self.outputFile, 'a') as file:
            file.write("\n CMD Queue CLEARED due to failure.")
        
    def stop(self):
        # print out the command queue if it was not empty
        if not self.commandQueue.empty():
            self.clearCommandQueue()
        else:
            # try to zero bc it was empty so commands likely work still
            if self.connectedEvent.is_set():
                self._sendCommand("Z")
        self.running = False
        if self.ser:
            self.ser.close()
            print(f"INFO SHIM CLIENT: Closed connection to arduino. bye.")
    
    def __del__(self):
        self.stop()

    # ______ SHIM SPECIFIC COMMANDS ______ #

    def requireShimDriverConnected(func):
        """Decorator to check if the EXSI client is connected before running a function."""
        def wrapper(self, *args, **kwargs):
            # Check the status of the event
            if not self.connectedEvent.is_set() and not self.debugging:
                # Show a message to the user, reconnect shim client.
                raise ShimDriverError("SHIM Client Not Connected")
            return func(self)
        return wrapper
 
    @launchInThread
    @requireShimDriverConnected
    def shimCalibrate(self):
        self.send("C")

    @launchInThread
    @requireShimDriverConnected
    def shimZero(self):
        self.send("Z")

    @launchInThread
    @requireShimDriverConnected
    def shimGetCurrent(self):
        # Could be used to double check that the channels calibrated
        self.send("I")
    
    @launchInThread
    @requireShimDriverConnected
    def shimSetCurrentManual(self, channel, current, board=0):
        """helper function to set the current for a specific channel on a specific board."""
        self.send(f"X {board} {channel} {current}")

class ShimDriverError(Exception):
    """Exception raised for errors in the Shim Driver."""
    pass
    
if __name__ == "__main__":
    arduino_port = "/dev/ttyACM1"  # Adjust to your Arduino's serial port
    # handle_serial(arduino_port)
