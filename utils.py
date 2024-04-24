import threading, time
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtCore import pyqtSignal, QObject, QThread, pyqtSignal

class LogMonitorThread(QThread):
    update_log = pyqtSignal(str)

    def __init__(self, filename, parent=None):
        super(LogMonitorThread, self).__init__(parent)
        self.filename = filename
        self.running = True

    def run(self):
        self.running = True
        with open(self.filename, 'r') as file:
            # Move to the end of the file
            file.seek(0, 2)
            while self.running:
                line = file.readline()
                if not line:
                    time.sleep(0.1)  # Sleep briefly to allow for a stop check
                    continue
                self.update_log.emit(line)

    def stop(self):
        self.running = False

def kickoff_thread(target, args=()):
    t = threading.Thread(target=target, args=args)
    t.daemon = True
    t.start()

def createMessageBox(title, text, informativeText):
    msg = QMessageBox()
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setWindowTitle(title)
    msg.setText(text)
    msg.setInformativeText(informativeText)
    msg.setStandardButtons(QMessageBox.StandardButton.Ok)
    return msg

def requireShimConnection(func):
    """Decorator to check if the EXSI client is connected before running a function."""
    def wrapper(self, *args, **kwargs):
        # Check the status of the event
        if not self.shimInstance.connectedEvent.is_set() and not self.debugging:
            # Show a message to the user, reconnect shim client.
            msg = createMessageBox("SHIM Client Not Connected",
                                   "The SHIM client is still not connected to shim arduino.", 
                                   "Closing Client.\nCheck that arduino is connected to the HV Computer via USB.\n" +
                                   "Check that the arduino port is set correctly using serial_finder.sh script.")
            msg.exec() 
            # have it close the exsi gui
            self.close()
            return
        return func(self)
    return wrapper

def requireExsiConnection(func):
    """Decorator to check if the EXSI client is connected before running a function."""
    def wrapper(self, *args, **kwargs):
        # Check the status of the event
        if not self.exsiInstance.connected_ready_event.is_set() and not self.debugging:
            # Show a message to the user, reconnect exsi client.
            msg = createMessageBox("EXSI Client Not Connected", 
                                   "The EXSI client is still not connected to scanner.", 
                                   "Closing Client.\nCheck that External Host on scanner computer set to 'newHV'.")
            msg.exec() 
            # have it close the exsi gui
            self.close()
            return
        return func(self)
    return wrapper

def requireAssetCalibration(func):
    """Decorator to check if the ASSET calibration scan is done before running a function."""
    def wrapper(self, *args, **kwargs):
        #TODO(rob): probably better to figure out how to look at existing scan state. somehow check all performed scans on start?
        if not self.assetCalibrationDone and not self.debugging:
            self.log("Debug: Need to do calibration scan before running scan with ASSET.")
            # Show a message to the user, reconnect exsi client.
            msg = createMessageBox("Asset Calibration Scan Not Performed",
                                   "Asset Calibration scan not detected to be completed.", 
                                   "Please perform calibration scan before continuing with this scan")
            msg.exec() 
            return
        return func(self)
    return wrapper


def disableSlowButtonsTillDone(func):
    """Decorator to wrap around slow button functions to disable other buttons until the function is done."""
    def wrapper(self, *args, **kwargs):
        for button in self.slowButtons:
            button.setEnabled(False)
        # TODO(rob): figure out a better way to handle args generically, and also debug print
        print(f"DEBUG: {func.__name__} called with len args  < 1: {len(args) < 1}, args: {args}")
        if len(args) < 1 or args[0] == False:
            func(self)
        else:
            func(self, *args)
        for button in self.slowButtons:
            button.setEnabled(True)
    return wrapper

class Trigger(QObject):
    finished = pyqtSignal()
