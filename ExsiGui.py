from datetime import datetime
import time, sys, paramiko, subprocess, os, threading
import numpy as np
import json

import signal
from PyQt6.QtWidgets import QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget, QTextEdit, QLabel, QSlider, QHBoxLayout, QLineEdit, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsTextItem, QTabWidget, QMessageBox
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap, QImage, QDoubleValidator, QIntValidator, QPainter

# Import the custom client classes and util functions
from exsi_client import exsi
from shim_client import shim
from dicomUtils import *
from shimCompute import *

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

class ExsiGui(QMainWindow):
    """
    The ExsiGui class represents the main GUI window for controlling the Exsi system.
    It provides functionality for connecting to the scanner client, the Shim client,
    and performing various operations related to calibration, scanning, and shimming.
    """

    def __init__(self, config):
        super().__init__()

        # tick this only when you continue to be devving...
        self.debugging = True

        self.examDateTime = datetime.now()
        self.examDateString = self.examDateTime.strftime('%Y%m%d_%H%M%S')

        self.config = config
        self.scannerLog = os.path.join(self.config['rootDir'], config['scannerLog'])
        self.shimLog = os.path.join(self.config['rootDir'], config['shimLog'])
        self.guiLog = os.path.join(self.config['rootDir'], config['guiLog'])

        # Create the log files if they don't exist / empty them if they already have things there
        for log in [self.scannerLog, self.shimLog, self.guiLog]:
            if not os.path.exists(log):
                # create the directory and file
                os.makedirs(os.path.dirname(log), exist_ok=True)
            with open(log, "w"):
                pass

        # Start the connection to the Shim client.
        # the requireShimConnection decorator will check if the connection is ready before executing any shim functionality.
        self.shimInstance = shim(self.config['shimPort'], self.config['shimBaudRate'], self.shimLog)

        # Start the connection to the scanner client.
        # The requireExsiConnection decorator will check if the connection is ready before executing any exsi functionality.
        self.exsiInstance = exsi(self.config['host'], self.config['exsiPort'], self.config['exsiProduct'], self.config['exsiPasswd'],
                                 self.shimZero, self.shimSetCurrentManual, self.scannerLog)

        # Setup the GUI
        self.initUI()

        # shim specific markers
        self.assetCalibrationDone = False
        self.autoPrescanDone = False
        self.obtainedBasisMaps = False
        self.computedShimCurrents = False

        # the results which are used to compute shim values
        self.background = None
        self.basisB0maps = []
        self.shimmedBackground = None
        self.roiMask = None

        # All the attributes for scan session that need to be None to start with.
        self.currentImageData = None
        self.currentImageTE = None
        self.currentImageOrientation = None
        self.gehcExamDataPath = None
        self.localExamRootDir = None
        

    ##### GUI LAYOUT RELATED FUNCTIONS #####   

    def initUI(self):

        self.setWindowAndExamNumber()
        self.setGeometry(100, 100, 1200, 600)

        self.centralTabWidget = QTabWidget()
        self.setCentralWidget(self.centralTabWidget)

        basicTab = QWidget()
        basicLayout = QVBoxLayout()
        self.setupBasicTabLayout(basicLayout)
        basicTab.setLayout(basicLayout)

        shimmingTab = QWidget()
        shimmingLayout = QVBoxLayout()
        self.setupShimmingTabLayout(shimmingLayout)
        shimmingTab.setLayout(shimmingLayout)

        self.setupTabAndWaitForConnection(basicTab, "EXSI Control", self.exsiInstance.connected_ready_event)
        self.setupTabAndWaitForConnection(shimmingTab, "SHIM Control", self.shimInstance.connectedEvent)
    
    def setupTabAndWaitForConnection(self, tab, tabName, connectedEvent):
        """Add a tab to the central tab widget and dynamically indicate if the client is connected."""
        name = tabName
        if not connectedEvent.is_set():
            name += " [!NOT CONNECTED]"
        self.centralTabWidget.addTab(tab, name)
        # wait for "connected event" to be set and rename the tab name removing the [!NOT CONNECTED]
        def renameTab():
            if not connectedEvent.is_set():
                connectedEvent.wait()
            index = self.centralTabWidget.indexOf(tab)
            self.log(f"Debug: renaming tab {index} to {tabName}")
            self.centralTabWidget.setTabText(index, tabName)
        t = threading.Thread(target=renameTab)
        t.daemon = True
        t.start()

    def setWindowAndExamNumber(self):
        # TODO(rob): make this look for exam instance to create local Exam root dir
        #       -- still somehow append a date so that you know the data you gen later
        # it does not make sense to do this till the exsi instance is connected, so we will move this to its own thread and then do it there on the connected event
        self.guiWindowTitle = lambda: f"[ Exsi Control GUI | EXAM: {self.exsiInstance.examNumber or '!'} | Patient: {self.exsiInstance.patientName or '!'} ]"
        self.setWindowTitle(self.guiWindowTitle())
        def setExamNumberAndName():
            if not self.exsiInstance.connected_ready_event.is_set():
                self.exsiInstance.connected_ready_event.wait()
            self.localExamRootDir = os.path.join(self.config['rootDir'], "data", self.exsiInstance.examNumber)
            if not os.path.exists(self.localExamRootDir):
                os.makedirs(self.localExamRootDir)
            # rename the window of the gui to include the exam number and patient name
            self.setWindowTitle(self.guiWindowTitle())
        t = threading.Thread(target=setExamNumberAndName)
        t.daemon = True
        t.start()
    
    def setupBasicTabLayout(self, layout):
        # basic layout is horizontal
        basicLayout = QHBoxLayout()

        # Setup QGraphicsView for image display
        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setFixedSize(512, 512)  # Set a fixed size for the view
        self.view.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        
        # Placeholder text setup, and the actual pixmap item
        self.placeholderText = QGraphicsTextItem("Waiting for image data")
        self.placeholderText.setPos(50, 250)  # Position the text appropriately within the scene
        self.scene.addItem(self.placeholderText)
        self.pixmapItem = QGraphicsPixmapItem()
        self.scene.addItem(self.pixmapItem)
        self.pixmapItem.setZValue(1)  # Ensure pixmap item is above the placeholder text

        # Slider for selecting slices
        self.sliceSlider = QSlider(Qt.Orientation.Horizontal)
        self.sliceSlider.valueChanged.connect(self.updateFromSliceSlider)
        # QLineEdit for manual slice entry
        self.sliceEntry = QLineEdit()
        self.sliceEntry.setValidator(QIntValidator(0, 0))  # Initial range will be updated
        self.sliceEntry.editingFinished.connect(self.updateFromSliceEntry)
        
        # Update the layout to include the QLineEdit
        imageLayout = QVBoxLayout()
        sliderLayout = QHBoxLayout()  # New layout for slider and line edit
        sliderLayout.addWidget(self.sliceSlider)
        sliderLayout.addWidget(self.sliceEntry)
        imageLayout.addWidget(self.view, alignment=Qt.AlignmentFlag.AlignCenter)
        imageLayout.addLayout(sliderLayout)  # Add the horizontal layout to the vertical layout

        # Add imageLayout to the basicLayout
        basicLayout.addLayout(imageLayout)

        # Controls and log layout
        controlsLayout = QVBoxLayout()
        self.setupExsiButtonsAndLog(controlsLayout)
        
        # Add controlsLayout to the basicLayout
        basicLayout.addLayout(controlsLayout)

        # Connect the log monitor
        self.exsiLogMonitorThread = LogMonitorThread(self.scannerLog)
        self.exsiLogMonitorThread.update_log.connect(self.updateExsiLogOutput)
        self.exsiLogMonitorThread.start()
        
        # Add the basic layout to the provided layout
        layout.addLayout(basicLayout)

    def setupShimmingTabLayout(self, layout):
        # Controls and log layout
        shimLayout = QVBoxLayout()

        self.setupShimButtonsAndLog(shimLayout)
        
        # Connect the log monitor
        self.shimLogMonitorThread = LogMonitorThread(self.shimLog)
        self.shimLogMonitorThread.update_log.connect(self.updateShimLogOutput)
        self.shimLogMonitorThread.start()
        
        # Add the basic layout to the provided layout
        layout.addLayout(shimLayout)

    def setupExsiButtonsAndLog(self, layout):
        # create the buttons
        self.doCalibrationScanButton = QPushButton("Do Calibration Scan")
        self.doCalibrationScanButton.clicked.connect(self.doCalibrationScan)
        self.doFgreScanButton = QPushButton("Do FGRE Scan")
        self.doFgreScanButton.clicked.connect(self.doFgreScan)
        self.renderLatestDataButton = QPushButton("Render Latest Data")
        self.renderLatestDataButton.clicked.connect(self.doTransferDataAndGetImage)

        self.exsiLogOutput = QTextEdit()
        self.exsiLogOutput.setReadOnly(True)
        self.exsiLogOutputLabel = QLabel("EXSI Log Output")
        # Add controls and log to controlsLayout
        layout.addWidget(self.doCalibrationScanButton)
        layout.addWidget(self.doFgreScanButton)
        layout.addWidget(self.renderLatestDataButton)


        layout.addWidget(self.exsiLogOutputLabel)
        layout.addWidget(self.exsiLogOutput)

    def setupShimButtonsAndLog(self, layout):
        # Shimming Basic Buttons
        self.doBasicShimLabel = QLabel("BASIC SHIM OPERATIONS")
        self.doShimCalibrateButton = QPushButton("Shim: Calibrate")
        self.doShimCalibrateButton.clicked.connect(self.shimCalibrate)
        self.doShimZeroButton = QPushButton("Shim: Zero")
        self.doShimZeroButton.clicked.connect(self.shimZero)
        self.doShimGetCurrentButton = QPushButton("Shim: Get Currents")
        self.doShimGetCurrentButton.clicked.connect(self.shimGetCurrent)

        # horizontal region for channel, current, and button
        self.shimChannelEntry = QLineEdit()
        self.shimChannelEntry.setValidator(QIntValidator(0, self.shimInstance.numLoops-1))
        self.shimCurrentEntry = QLineEdit()
        self.shimCurrentEntry.setValidator(QDoubleValidator(-2.4, 2.4, 2))
        self.doShimSetCurrentButton = QPushButton("Shim: Set Current")
        self.doShimSetCurrentButton.clicked.connect(self.shimSetCurrent)
        setShimCurrentLayout = QHBoxLayout()
        setShimCurrentLayout.addWidget(self.shimChannelEntry)
        setShimCurrentLayout.addWidget(self.shimCurrentEntry)
        setShimCurrentLayout.addWidget(self.doShimSetCurrentButton)

        # Actual Shim Calibation and function operations
        self.doShimProcedureLabel = QLabel("MACRO SHIM OPERATIONS")
        # macro for obtaining background scans for every loop
        self.doLoopCalibrationScansButton = QPushButton("Shim: Perform Background B0map Scans")
        self.doLoopCalibrationScansButton.clicked.connect(self.doBasisPairScan)
        # mega macro for performing all calibrations scans for every loop
        self.doLoopCalibrationScansButton = QPushButton("Shim: Perform Loop Calibration B0map Scans")
        self.doLoopCalibrationScansButton.clicked.connect(self.doLoopCalibrationScans)

        self.shimLogOutput = QTextEdit()
        self.shimLogOutput.setReadOnly(True)
        self.shimLogOutputLabel = QLabel("SHIM Log Output")

        layout.addWidget(self.doBasicShimLabel)
        layout.addWidget(self.doShimCalibrateButton)
        layout.addWidget(self.doShimZeroButton)
        layout.addWidget(self.doShimGetCurrentButton)
        layout.addLayout(setShimCurrentLayout)

        layout.addWidget(self.doShimProcedureLabel)
        layout.addWidget(self.doLoopCalibrationScansButton)

        layout.addWidget(self.shimLogOutputLabel)
        layout.addWidget(self.shimLogOutput)

    ##### GRAPHICS FUNCTION DEFINITIONS #####   

    def reconnectClient(self):
        # TODO(rob): add buttons to relaunch the clients if they die
        pass

    def updateImageDisplay(self, sliceIndex):
        if self.currentImageData is not None:
            # Update the slider range based on the new data
            depth = self.currentImageData.shape[0]
            self.sliceSlider.setMinimum(0)
            self.sliceSlider.setMaximum(depth - 1)
            self.sliceEntry.setValidator(QIntValidator(0, depth - 1))

            # If sliceIndex is None or out of new bounds, default to first slice
            if sliceIndex is None or sliceIndex >= depth:
                sliceIndex = 0
            self.sliceSlider.setValue(sliceIndex)
            
            # The rest of your image display logic remains the same...
            sliceData = np.ascontiguousarray(self.currentImageData[sliceIndex])
            # Extract the slice and normalize it
            sliceData = self.currentImageData[sliceIndex].astype(float)  # Convert to float for normalization
            normalizedData = (sliceData - sliceData.min()) / (sliceData.max() - sliceData.min()) * 255
            displayData = normalizedData.astype(np.uint8)  # Convert to uint8
            height, width = displayData.shape
            bytesPerLine = displayData.strides[0] 
            qImage = QImage(displayData.data, width, height, bytesPerLine, QImage.Format.Format_Grayscale8)
            if qImage.isNull():
                self.log("Debug: Failed to create QImage")
            else:
                pixmap = QPixmap.fromImage(qImage)
                self.pixmapItem.setPixmap(pixmap)
                self.scene.setSceneRect(self.pixmapItem.boundingRect())  # Adjust scene size to the pixmap's bounding rect
                self.view.fitInView(self.pixmapItem, Qt.AspectRatioMode.KeepAspectRatio)  # Fit the view to the item
                self.placeholderText.setVisible(False)
                self.view.viewport().update()  # Force the viewport to update

        else:
            self.placeholderText.setVisible(True)

    def updateFromSliceEntry(self):
        # Update the display based on the manual entry in QLineEdit
        sliceIndex = int(self.sliceEntry.text()) if self.sliceEntry.text() else 0
        self.updateImageDisplay(sliceIndex)

    def updateFromSliceSlider(self, value):
        # Directly update the line edit when the slider value changes
        self.sliceEntry.setText(str(value))
        self.updateImageDisplay(value)

    def updateExsiLogOutput(self, text):
        self.exsiLogOutput.append(text)

    def updateShimLogOutput(self, text):
        self.shimLogOutput.append(text)
    
    ##### HELPER FUNCTIONS FOR EXSI CONTROL BUTTONS #####
    
    def iterateBasisPairScan(self):
        # TODO(rob): eventually add these to the config file
        cvs = {"act_tr": 3300, "act_te": [1104, 1604], "rhrcctrl": 13, "rhimsize": 64}
        for i in range(2):
            self.sendSelTask()
            for cv in cvs.keys():
                if cv == "act_te":
                    self.sendSetCV(cv, cvs[cv][i])
                else:
                    self.sendSetCV(cv, cvs[cv])
            self.sendActTask()
            self.sendPatientTable()
            if not self.autoPrescanDone:
                self.sendPrescan(auto=True)
                self.autoPrescanDone = True
            else:
                self.sendPrescan(auto=False)
            self.sendScan()

    def queueLoadWithCaliCurrentSet(self, channelNum):
        # when the exsiclient gets this specific command, it will know to dispatch both the loadProtocol 
        # command and also a Zero Current and setCurrent to channelNum with calibration current of 1.0
        self.sendLoadProtocol(f"ConformalShimCalibration3 | {channelNum} 1.0")

    def doBasisPairScan(self):
        # Basic basis pair scan. should be used to scan the background
        self.sendLoadProtocol("ConformalShimCalibration3")
        self.iterateBasisPairScan()

    def doCaliBasisPairScan(self, channelNum):
        self.queueLoadWithCaliCurrentSet(channelNum)
        self.iterateBasisPairScan()

    def requireExsiConnection(func):
        """Decorator to check if the EXSI client is connected before running a function."""
        def wrapper(self, *args, **kwargs):
            # Check the status of the event
            if not self.exsiInstance.connected_ready_event.is_set():
                # Show a message to the user, reconnect exsi client.
                msg = QMessageBox()
                msg.setIcon(QMessageBox.Icon.Warning)
                msg.setWindowTitle("EXSI Client Not Connected")
                msg.setText("The EXSI client is still not connected to scanner.")
                msg.setInformativeText("Closing Client.\nCheck that External Host on scanner computer set to 'newHV'.")
                msg.setStandardButtons(QMessageBox.StandardButton.Ok)
                msg.exec() 
                # have it close the exsi gui
                self.close()
                return
            return func(self)
        return wrapper
    
    def requireAssetCalibration(func):
        """Decorator to check if the ASSET calibration scan is done before running a function."""
        def wrapper(self, *args, **kwargs):
            if not self.assetCalibrationDone:
                #TODO(rob): probably better to figure out how to look at existing scan state. somehow check all performed scans on start?
                self.log("Debug: Need to do calibration scan before running scan with ASSET.")
                # Show a message to the user, reconnect exsi client.
                msg = QMessageBox()
                msg.setIcon(QMessageBox.Icon.Warning)
                msg.setWindowTitle("Asset Calibration Scan Not Performed")
                msg.setText("Asset Calibration scan not detected to be completed.")
                msg.setInformativeText("Please perform calibration scan before continuing with this scan")
                msg.setStandardButtons(QMessageBox.StandardButton.Ok)
                msg.exec() 
                return
            return func(self)
        return wrapper

    ##### MACRO FUNCTION DEFINITIONS; These are the functions that handle button clicks #####   

    @requireExsiConnection
    def doTransferDataAndGetImage(self):
        self.transferScanData()
        if os.path.exists(self.localExamRootDir):
            self.getLatestImage(stride=1)
        else:
            self.log("Debug: local directory has not been made yet...")

    @requireExsiConnection
    def doCalibrationScan(self):
        # dont need to do the assetCalibration scan more than once
        if self.exsiInstance and not self.assetCalibrationDone:
            self.sendLoadProtocol("ConformalShimCalibration4")
            self.sendSelTask()
            self.sendActTask()
            self.sendPatientTable()
            self.sendScan()
            if self.exsiInstance.images_ready_event.wait(timeout=120):
                self.assetCalibrationDone = True
                self.exsiInstance.images_ready_event.clear()
    
    @requireExsiConnection
    @requireAssetCalibration
    def doFgreScan(self):
        if self.exsiInstance:
            self.sendLoadProtocol("ConformalShimCalibration5")
            self.sendSelTask()
            self.sendActTask()
            self.sendPatientTable()
            self.sendScan()
            if not self.exsiInstance.images_ready_event.wait(timeout=120):
                self.log(f"Debug: scan didn't complete")
            else:
                self.exsiInstance.images_ready_event.clear()
                self.transferScanData()
                self.getLatestImage(stride=1)
    
    @requireExsiConnection
    @requireAssetCalibration
    def doLoopCalibrationScans(self):
        """Perform all the calibration scans for each loop in the shim system."""
        for i in range(self.shimInstance.numLoops):
            self.doCaliBasisPairScan(i)

    ##### EXSI CLIENT CONTROL FUNCTIONS #####   

    def sendLoadProtocol(self, name):
        if self.exsiInstance:
            self.exsiInstance.send('LoadProtocol site path="' + name + '"')

    def sendSelTask(self):
        if self.exsiInstance:
            self.exsiInstance.send('SelectTask taskkey=')

    def sendActTask(self):
        if self.exsiInstance:
            self.exsiInstance.send('ActivateTask')

    def sendPatientTable(self):
        if self.exsiInstance:
            self.exsiInstance.send('PatientTable advanceToScan')

    def sendScan(self):
        if self.exsiInstance:
            self.exsiInstance.send('Scan')

    def sendGetExamInfo(self):
        if self.exsiInstance:
            self.exsiInstance.send('GetExamInfo')
    
    def sendSetCV(self, name, value):
        if self.exsiInstance:
            self.exsiInstance.send(f"SetCVs {name}={value}")

    def sendPrescan(self, auto=False):
        if self.exsiInstance:
            if auto:
                self.exsiInstance.send("Prescan auto")
            else:
                self.exsiInstance.send("Prescan skip")

    ##### SHIM COMPUTATION FUNCTIONS #####   

    def requireShimConnection(func):
        """Decorator to check if the EXSI client is connected before running a function."""
        def wrapper(self, *args, **kwargs):
            # Check the status of the event
            if not self.shimInstance.connectedEvent.is_set():
                # Show a message to the user, reconnect shim client.
                msg = QMessageBox()
                msg.setIcon(QMessageBox.Icon.Warning)
                msg.setWindowTitle("SHIM Client Not Connected")
                msg.setText("The SHIM client is still not connected to shim arduino.")
                msg.setInformativeText("Closing Client.\nCheck that arduino is connected to the HV Computer via USB.\
                                       \nCheck that the arduino port is set correctly using serial_finder.sh script.")
                msg.setStandardButtons(QMessageBox.StandardButton.Ok)
                msg.exec() 
                # have it close the exsi gui
                self.close()
                return
            return func(self)
        return wrapper

    def computeShimCurrents(self):
        # assumes that you have gotten background by doBasisPairScan and also doLoopCalibrationScans
        # you also need to have transferred them into the local exam root directory to use them..
        
        # first compute b0maps of the background and also the 
        b0maps = compute_b0maps(self.shimInstance.numLoops+1, self.localExamRootDir)

        self.background = b0maps[0]
        self.basisB0maps = subtractBackground(b0maps)

        # TODO(rob): add the slider for slice index
        self.roiMask = creatMask(self.background, self.basisB0maps, roi=None, sliceIndex=30)

        self.currents = solveCurrents(self.background, self.basisB0maps, self.roiMask)

        self.shimmedBackground = self.background.copy()
        for i in range(self.shimInstance.numLoops):
            self.shimmedBackground += self.currents[i] * self.basisB0maps[i]

        # TODO(rob): add all the hooks to update the gui from here or maybe from the UI area of this code...

    ##### SHIM CLIENT CONTROL FUNCTIONS #####   

    @requireShimConnection
    def shimCalibrate(self):
        if self.shimInstance:
            self.shimInstance.send("C")

    @requireShimConnection
    def shimZero(self):
        if self.shimInstance:
            self.shimInstance.send("Z")

    @requireShimConnection
    def shimGetCurrent(self):
        # Could be used to double check that the channels calibrated
        if self.shimInstance:
            self.shimInstance.send("I")
    
    @requireShimConnection
    def shimSetCurrent(self):
        # get the values from the fields above
        board = 0
        if not self.shimCurrentEntry.text() or not self.shimChannelEntry.text():
            return
        if self.shimInstance:
            self.shimInstance.send(f"X {board} {int(self.shimChannelEntry.text())} {float(self.shimCurrentEntry.text())}")

    def shimSetCurrentManual(self, channel, current, board=0):
        if self.shimInstance:
            self.shimInstance.send(f"X {board} {channel} {current}")
    
    @requireShimConnection
    def shimSetAllCurrents(self, currents):
        for i in range(self.shimInstance.numLoops):
            self.shimSetCurrentManual(i%8, currents[i], i//8)

    ##### SCAN DATA RELATED FUNCTIONS #####   

    # TODO(rob): move most of these transfer functions into their own UTIL file. dataUtils.py or smth
    def execSSHCommand(self, command):
        # Initialize the SSH client
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # Automatically add host key
        try:
            client.connect(hostname=self.config['host'], port=self.config['hvPort'], username=self.config['hvUser'], password=self.config['hvPassword'])
            stdin, stdout, stderr = client.exec_command(command)
            return stdout.readlines()  # Read the output of the command

        except Exception as e:
            self.log(f"Connection or command execution failed: {e}")
        finally:
            client.close()

    def execSCPCommand(self, source, destination):
        # Construct the SCP command using sshpass
        cmd = f"sshpass -p {self.config['hvPassword']} scp -r {self.config['hvUser']}@{self.config['host']}:{source} {destination}"

        # Execute the SCP command
        process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Wait for the command to complete
        stdout, stderr = process.communicate()

        # Check if the command was executed successfully
        if process.returncode == 0:
            return stdout.decode('utf-8')
        else:
            return f"Error: {stderr.decode('utf-8')}"

    def execRsyncCommand(self, source, destination):
        # Construct the SCP command using sshpass
        cmd = f"sshpass -p {self.config['hvPassword']} rsync -avz {self.config['hvUser']}@{self.config['host']}:{source} {destination}"

        # Execute the SCP command
        process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Wait for the command to complete
        stdout, stderr = process.communicate()
        
        # Check if the command was executed successfully
        if process.returncode == 0:
            return stdout.decode('utf-8')
        else:
            return f"Error: {stderr.decode('utf-8')}"
            
    def setGehcExamDataPath(self):
        if self.exsiInstance.examNumber is None:
            self.log("Error: No exam number found in the exsi client instance.")
            return
        exam_number = self.exsiInstance.examNumber
        output = self.execSSHCommand("pathExtract "+exam_number)
        if output:
            last_line = output[-1].strip() 
        else:
            return
        parts = last_line.split("/")
        self.gehcExamDataPath = os.path.join("/", *parts[:7])
        self.log(f"Debug: obtained exam data path: {self.gehcExamDataPath}")

    def transferScanData(self):
        self.log(f"Debug: initiating transfer.")
        if self.gehcExamDataPath is None:
            self.setGehcExamDataPath()
            # perform initial transfer
            self.execSCPCommand(self.gehcExamDataPath, self.localExamRootDir)
            self.log(f"Debug: Getting initial data and using scp")
        else:
            self.execRsyncCommand(self.gehcExamDataPath + '/*', self.localExamRootDir)
            self.log(f"Debug: already got some data and using rsync")

    def execBashCommand(self, cmd):
        # TODO(rob) remove this function because i think it is not used at all...
        # Execute the bash command
        process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Wait for the command to complete
        stdout, stderr = process.communicate()
        
        # Check if the command was executed successfully
        if process.returncode == 0:
            return stdout.decode('utf-8')
        else:
            return f"Error: {stderr.decode('utf-8')}"

    def getLatestImage(self, stride=1, offset=0):
        self.log(f"Debug: local exam root {self.localExamRootDir}") 
        latestDCMDir = listSubDirs(self.localExamRootDir)[-1]
        res = extractBasicImageData(latestDCMDir, stride, offset)
        self.currentImageData, self.currentImageTE, self.currentImageOrientation = res
        self.log(f"Debug: obtained image here with this shape and type: {self.currentImageData.shape}, {self.currentImageData.dtype}")
        self.log(f"Debug: obtained image with TE and orientation: {self.currentImageTE}, {self.currentImageOrientation}")
        sliceIndex = int(self.sliceEntry.text()) if self.sliceEntry.text() else 0
        self.updateImageDisplay(sliceIndex)

    ##### OTHER METHODS ######

    def log(self, msg, forceStdOut=False):
        # record a timestamp and prepend to the message
        current_time = datetime.now()
        formatted_time = current_time.strftime('%H:%M:%S')
        msg = f"{formatted_time} {msg}"
        # only print if in debugging mode, or if forceStdOut is set to True
        if self.debugging or forceStdOut:
            print(msg)
        # always write to the log file
        with open(self.guiLog, 'a') as file:
            file.write(f"{msg}\n")

    def closeEvent(self, event):
        self.log("INFO: Starting to close", True)
        if self.exsiLogMonitorThread:
            self.exsiLogMonitorThread.stop()
            self.exsiLogMonitorThread.wait()
        if self.shimLogMonitorThread:
            self.shimLogMonitorThread.stop()
            self.shimLogMonitorThread.wait()
        self.log("INFO: Done with logmonitorthread", True)
        if self.exsiInstance:
            self.log("INFO: Stopping exsi client instance", True)
            self.exsiInstance.stop()
        if self.shimInstance:
            self.log("INFO: Stopping shim client instance", True)
            self.shimInstance.stop()
        self.log("INFO: Done with exsi instance", True)
        event.accept()
        super().closeEvent(event)

def handle_exit(signal_received, frame):
    # Handle any cleanup here
    print('SIGINT or CTRL-C detected. Exiting gracefully.')
    QApplication.quit()

def load_config(filename):
    with open(filename, 'r') as file:
        return json.load(file)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)
    
    # try:
    config = load_config('config.json')
    app = QApplication(sys.argv)
    ex = ExsiGui(config)
    ex.show()
    sys.exit(app.exec())
