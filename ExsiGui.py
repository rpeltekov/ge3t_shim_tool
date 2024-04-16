from datetime import datetime
import time, sys, paramiko, subprocess, os, threading
import numpy as np
import json

import signal
from PyQt6.QtWidgets import QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget, QTextEdit, QLabel, QSlider, QHBoxLayout, QLineEdit, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsTextItem, QTabWidget, QMessageBox, QCheckBox, QSizePolicy, QButtonGroup, QRadioButton
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

        # shim specific markers
        self.assetCalibrationDone = False
        self.autoPrescanDone = False
        self.obtainedBasisMaps = False
        self.computedShimCurrents = False

        # the results which are used to compute shim values
        self.shimSliceIndex = 20 # default slice index to shim at
        self.background = None
        self.rawBasisB0maps = []
        self.basisB0maps = []
        self.currents = []
        self.expShimmedBackground = None
        self.shimmedBackground = None
        self.roiMask = None

        # All the attributes for scan session that need to be None to start with.
        self.currentImageData = None
        self.currentImageTE = None
        self.currentImageOrientation = None
        self.gehcExamDataPath = None
        self.localExamRootDir = None

        # Setup the GUI
        self.initUI()
        

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
        # TODO(rob): still somehow append a date so that you know the data you gen later
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
        self.roiScene = QGraphicsScene()
        self.roiView = QGraphicsView(self.roiScene)
        self.roiView.setFixedSize(512, 512)  # Set a fixed size for the view
        self.roiView.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        
        # Placeholder text setup, and the actual pixmap item
        self.placeholderText = QGraphicsTextItem("Waiting for image data")
        self.placeholderText.setPos(50, 250)  # Position the text appropriately within the scene
        self.roiScene.addItem(self.placeholderText)
        self.roiPixmapItem = QGraphicsPixmapItem()
        self.roiScene.addItem(self.roiPixmapItem)
        self.roiPixmapItem.setZValue(1)  # Ensure pixmap item is above the placeholder text

        # TODO(rob): use the new helpers that I defined below
        # Slider for selecting slices
        self.roiSliceSlider = QSlider(Qt.Orientation.Horizontal)
        self.roiSliceSlider.valueChanged.connect(self.updateFromROISliceSlider)
        # QLineEdit for manual slice entry
        self.roiSliceEntry = QLineEdit()
        self.roiSliceEntry.setValidator(QIntValidator(0, 0))  # Initial range will be updated
        self.roiSliceEntry.editingFinished.connect(self.updateFromSliceROIEntry)
        
        # Update the layout to include the QLineEdit
        imageLayout = QVBoxLayout()
        sliderLayout = QHBoxLayout()  # New layout for slider and line edit
        sliderLayout.addWidget(self.roiSliceSlider)
        sliderLayout.addWidget(self.roiSliceEntry)
        imageLayout.addWidget(self.roiView, alignment=Qt.AlignmentFlag.AlignCenter)
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

    def setupShimButtonsAndLog(self, layout):
        # shim window is split down the middle to begin with
        shimSplitLayout = QHBoxLayout()
        layout.addLayout(shimSplitLayout)

        # make the left and right vboxex
        leftLayout = QVBoxLayout()
        rightLayout = QVBoxLayout()
        shimSplitLayout.addLayout(leftLayout)
        shimSplitLayout.addLayout(rightLayout)

        # LEFT SIDE

        # add radio button group for selecting which view you want to see....
        # Create the QButtonGroup
        shimVizButtonWindow = QWidget()
        shimVizButtonLayout = QHBoxLayout()
        self.shimVizButtonGroup = QButtonGroup(shimVizButtonWindow)
        leftLayout.addLayout(shimVizButtonLayout)

        # Create the radio buttons
        shimVizButtonBackground = QRadioButton("Background")
        shimVizButtonEstBackground = QRadioButton("Estimated Shimmed Background")
        shimVizButtonShimmedBackground = QRadioButton("Actual Shimmed Background")
        shimVizButtonBackground.setChecked(True)  # Default to background

        # Add the radio buttons to the button group
        self.shimVizButtonGroup.addButton(shimVizButtonBackground)
        shimVizButtonLayout.addWidget(shimVizButtonBackground)
        self.shimVizButtonGroup.addButton(shimVizButtonEstBackground)
        shimVizButtonLayout.addWidget(shimVizButtonEstBackground)
        self.shimVizButtonGroup.addButton(shimVizButtonShimmedBackground)
        shimVizButtonLayout.addWidget(shimVizButtonShimmedBackground)

        # add another graphics scene visualizer
        
        # Setup QGraphicsView for image display
        self.shimScene = QGraphicsScene()
        self.shimView = QGraphicsView(self.shimScene)
        leftLayout.addWidget(self.shimView, alignment=Qt.AlignmentFlag.AlignCenter)
        self.shimView.setFixedSize(512, 512)  # Set a fixed size for the view
        self.shimView.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        
        # Placeholder text setup, and the actual pixmap item
        self.placeholderText = QGraphicsTextItem("Waiting for image data")
        self.placeholderText.setPos(50, 250)  # Position the text appropriately within the scene
        self.shimScene.addItem(self.placeholderText)
        self.shimPixmapItem = QGraphicsPixmapItem()
        self.shimScene.addItem(self.shimPixmapItem)
        self.shimPixmapItem.setZValue(1)  # Ensure pixmap item is above the placeholder text

        # add the statistics output using another non editable textedit widget
        self.shimStatText = QTextEdit()
        self.shimStatText.setReadOnly(True)
        self.shimStatTextLabel = QLabel("Image Statistics")
        leftLayout.addWidget(self.shimStatTextLabel)
        leftLayout.addWidget(self.shimStatText)


        # RIGHT SIDE

        # MANUAL SHIMMING OPERATIONS START
        # horizontal box to split up the calibrate zero and get current buttons from the manual set current button and entries 
        self.domanualShimLabel = QLabel("MANUAL SHIM OPERATIONS")
        rightLayout.addWidget(self.domanualShimLabel)
        manualShimLayout = QHBoxLayout()
        rightLayout.addLayout(manualShimLayout)

        calZeroGetcurrentLayout = QVBoxLayout()
        manualShimLayout.addLayout(calZeroGetcurrentLayout)

        # add the calibrate zero and get current buttons to left of manualShimLayout
        manualShimButtonsLayout = QVBoxLayout()
        manualShimLayout.addLayout(manualShimButtonsLayout)
        self.shimCalChannelsButton = self.addButtonConnectedToFunction(manualShimButtonsLayout, "Calibrate Shim Channels", self.shimCalibrate)
        self.shimZeroButton        = self.addButtonConnectedToFunction(manualShimButtonsLayout, "Zero Shim Channels", self.shimZero)
        self.shimGetCurrentsButton = self.addButtonConnectedToFunction(manualShimButtonsLayout, "Get Shim Currents", self.shimGetCurrent)

        # add the vertical region for channel input, current input, and set current button right of manualShimLayout
        setChannelCurrentShimLayout = QVBoxLayout()
        manualShimLayout.addLayout(setChannelCurrentShimLayout)
        self.shimManualChannelEntry = self.addEntryWithLabel(setChannelCurrentShimLayout, "Channel Index (Int): ", QIntValidator(0, self.shimInstance.numLoops-1))
        self.shimManualCurrenEntry = self.addEntryWithLabel(setChannelCurrentShimLayout, "Current (A): ", QDoubleValidator(-2.4, 2.4, 2))
        self.shimManualSetCurrentButton = self.addButtonConnectedToFunction(setChannelCurrentShimLayout, "Shim: Set Currents", self.shimSetCurrent)

        # ACTUAL SHIM OPERATIONS START
        # Just add the rest of the things to the vertical layout.
        # add a label and select for the slice index
        self.shimSliceIndexSlider, self.shimSliceIndexEntry = self.addLabeledSliderAndEntry(rightLayout, "Slice Index (Int): ", QIntValidator(0, 2147483647)) #TODO(rob): validate this after somehow....
        self.shimSliceIndexEntry.setText(str(self.shimSliceIndex))

        self.doShimProcedureLabel = QLabel("SHIM OPERATIONS")
        rightLayout.addWidget(self.doShimProcedureLabel)

        # macro for obtaining background scans
        self.doBackgroundScansButton, self.doBackgroundScansMarker = self.addButtonWithFuncAndMarker(rightLayout, "Shim: Perform Background B0map Scans", self.doBackgroundScans)
        # mega macro for performing all calibrations scans for every loop
        self.doLoopCalibrationScansButton, self.doLoopCalibrationScansMarker = self.addButtonWithFuncAndMarker(rightLayout, "Shim: Perform Loop Calibration B0map Scans", self.doLoopCalibrationScans)
        # macro for setting All computed shim currents on the driver
        setAllCurrentsLayout = QHBoxLayout()
        rightLayout.addLayout(setAllCurrentsLayout)
        self.currentsComputedMarker = QCheckBox("Currents Computed?")
        self.currentsComputedMarker.setEnabled(False)
        self.currentsComputedMarker.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        setAllCurrentsLayout.addWidget(self.currentsComputedMarker)
        self.setAllCurrentsButton, self.setAllCurrentsMarker = self.addButtonWithFuncAndMarker(setAllCurrentsLayout, "Shim: Set All Computed Currents", self.shimSetAllCurrents)
        # macro for obtaining shimmed background scan 
        self.doShimmedScansButton, self.doShimmedScansMarker = self.addButtonWithFuncAndMarker(rightLayout, "Shim: Perform Shimmed Eval Scans", self.doShimmedScans)

        # Add the log output here
        self.shimLogOutput = QTextEdit()
        self.shimLogOutput.setReadOnly(True)
        self.shimLogOutputLabel = QLabel("SHIM Log Output")
        rightLayout.addWidget(self.shimLogOutputLabel)
        rightLayout.addWidget(self.shimLogOutput)

    def addButtonConnectedToFunction(self, layout, buttonName, function):
        button = QPushButton(buttonName)
        button.clicked.connect(function)
        layout.addWidget(button)
        return button

    def addEntryWithLabel(self, layout, labelStr, entryvalidator):
        label = QLabel(labelStr)
        entry = QLineEdit()
        entry.setValidator(entryvalidator)
        labelEntryLayout = QHBoxLayout()
        labelEntryLayout.addWidget(label)
        labelEntryLayout.addWidget(entry)
        layout.addLayout(labelEntryLayout)
        return entry

    def addLabeledSliderAndEntry(self, layout, labelStr, entryvalidator):
        slider = QSlider(Qt.Orientation.Horizontal)
        label = QLabel(labelStr)
        entry = QLineEdit()
        entry.setValidator(entryvalidator)
        labelEntryLayout = QHBoxLayout()
        labelEntryLayout.addWidget(label)
        labelEntryLayout.addWidget(slider)
        labelEntryLayout.addWidget(entry)
        layout.addLayout(labelEntryLayout)
        return slider, entry
    
    def addButtonWithFuncAndMarker(self, layout, buttonName, function):
        hlayout = QHBoxLayout()
        layout.addLayout(hlayout)
        marker = QCheckBox("Done?")
        marker.setEnabled(False)
        marker.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        button = self.addButtonConnectedToFunction(hlayout, buttonName, function)
        hlayout.addWidget(marker)
        return button, marker

    ##### GRAPHICS FUNCTION DEFINITIONS #####   

    def reconnectClient(self):
        # TODO(rob): add buttons to relaunch the clients if they die
        pass

    def updateROIImageDisplay(self, roiSliceIndex):
        if self.currentImageData is not None:
            # Update the slider range based on the new data
            depth = self.currentImageData.shape[0]
            self.roiSliceSlider.setMinimum(0)
            self.roiSliceSlider.setMaximum(depth - 1)
            self.roiSliceEntry.setValidator(QIntValidator(0, depth - 1))

            # If roiSliceIndex is None or out of new bounds, default to first slice
            if roiSliceIndex is None or roiSliceIndex >= depth:
                roiSliceIndex = 0
            self.roiSliceSlider.setValue(roiSliceIndex)
            
            # The rest of your image display logic remains the same...
            sliceData = np.ascontiguousarray(self.currentImageData[roiSliceIndex])
            # Extract the slice and normalize it
            sliceData = self.currentImageData[roiSliceIndex].astype(float)  # Convert to float for normalization
            normalizedData = (sliceData - sliceData.min()) / (sliceData.max() - sliceData.min()) * 255
            displayData = normalizedData.astype(np.uint8)  # Convert to uint8
            height, width = displayData.shape
            bytesPerLine = displayData.strides[0] 
            qImage = QImage(displayData.data, width, height, bytesPerLine, QImage.Format.Format_Grayscale8)
            if qImage.isNull():
                self.log("Debug: Failed to create QImage")
            else:
                pixmap = QPixmap.fromImage(qImage)
                self.roiPixmapItem.setPixmap(pixmap)
                self.roiScene.setSceneRect(self.roiPixmapItem.boundingRect())  # Adjust scene size to the pixmap's bounding rect
                self.roiView.fitInView(self.roiPixmapItem, Qt.AspectRatioMode.KeepAspectRatio)  # Fit the view to the item
                self.placeholderText.setVisible(False)
                self.roiView.viewport().update()  # Force the viewport to update

        else:
            self.placeholderText.setVisible(True)

    def updateFromSliceROIEntry(self):
        # Update the display based on the manual entry in QLineEdit
        roiSliceIndex = int(self.roiSliceEntry.text()) if self.roiSliceEntry.text() else 0
        self.updateROIImageDisplay(roiSliceIndex)

    def updateFromROISliceSlider(self, value):
        # Directly update the line edit when the slider value changes
        self.roiSliceEntry.setText(str(value))
        self.updateROIImageDisplay(value)

    def updateExsiLogOutput(self, text):
        self.exsiLogOutput.append(text)

    def updateShimLogOutput(self, text):
        self.shimLogOutput.append(text)
    
    ##### HELPER FUNCTIONS FOR EXSI CONTROL BUTTONS !!!! NO BUTTON SHOULD MAP TO THIS #####
    
    def iterateBasisPairScan(self):
        """once the b0map sequence is loaded, subroutines are iterated along with cvs to obtain basis maps."""
        # TODO(rob): eventually add these to the config file
        cvs = {"act_tr": 3300, "act_te": [1104, 1604], "rhrcctrl": 13, "rhimsize": 64}
        for i in range(2):
            self.sendSelTask()
            self.sendActTask()
            for cv in cvs.keys():
                if cv == "act_te":
                    self.log("Debug: Setting act_te to " + str(cvs[cv][i]))
                    self.sendSetCV(cv, cvs[cv][i])
                else:
                    self.sendSetCV(cv, cvs[cv])
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

    def shimSetCurrentManual(self, channel, current, board=0):
        """helper function to set the current for a specific channel on a specific board."""
        if self.shimInstance:
            self.shimInstance.send(f"X {board} {channel} {current}")

    def countScansCompleted(self, n):
        """should be 2 for every basis pair scan"""
        for _ in range(n):
            if not self.exsiInstance.images_ready_event.wait(timeout=180):
                self.log(f"Error: scan didn't complete within 180 seconds bruh")
                # TODO(rob) probably should raise some sorta error here...
        # after scans get completed, go ahead and get the latest scan data over on this machine...
        self.transferScanData()

    def triggerComputeShimCurrents(self):
        """if background and basis maps are obtained, compute the shim currents"""
        if self.doBackgroundScansMarker.isChecked() and self.doLoopCalibrationScansMarker.isChecked():
            self.computeShimCurrents()
            self.currentsComputedMarker.setChecked(True)

    ##### REQUIRE DECORATORS #####

    def createMessageBox(self, title, text, informativeText):
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
            if not self.shimInstance.connectedEvent.is_set():
                # Show a message to the user, reconnect shim client.
                msg = self.createMessageBox("SHIM Client Not Connected",
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
            if not self.exsiInstance.connected_ready_event.is_set():
                # Show a message to the user, reconnect exsi client.
                msg = self.createMessageBox("EXSI Client Not Connected", 
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
            if not self.assetCalibrationDone and not self.debugging:
                #TODO(rob): probably better to figure out how to look at existing scan state. somehow check all performed scans on start?
                self.log("Debug: Need to do calibration scan before running scan with ASSET.")
                # Show a message to the user, reconnect exsi client.
                msg = self.createMessageBox("Asset Calibration Scan Not Performed",
                                            "Asset Calibration scan not detected to be completed.", 
                                            "Please perform calibration scan before continuing with this scan")
                msg.exec() 
                return
            return func(self)
        return wrapper

    ##### BUTTON FUNCTION DEFINITIONS; These are the functions that handle button click #####   
    # as such they should all be decorated in some fashion to not allow for operations to happen if they cannot

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
        if not self.shimManualCurrenEntry.text() or not self.shimManualChannelEntry.text():
            return
        self.shimSetCurrentManual(int(self.shimManualChannelEntry.text()), float(self.shimManualCurrenEntry.text()), board)

    #### More macro type of button presses

    @requireExsiConnection
    def doTransferDataAndGetImage(self):
        self.transferScanData()
        if os.path.exists(self.localExamRootDir):
            self.getLatestROIImage(stride=1)
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
            # TODO(rob): make this a part of the decorator function to grey out related buttons
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
                self.getLatestROIImage(stride=1)
    
    @requireExsiConnection
    @requireShimConnection
    @requireAssetCalibration
    def doBackgroundScans(self):
        # Perform the background scans for the shim system.
        def setMarker():
            self.doBackgroundScansMarker.setChecked(False)
            self.shimZero() # NOTE(rob): Hopefully this zeros quicker that the scans get set up...
            self.background = None
            self.exsiInstance.images_ready_event.clear()
            self.countScansCompleted(2)
            self.doBackgroundScansMarker.setChecked(True)
            self.log("DEBUG: just finished all the background scans")
            self.computeBackgroundB0map()
            # if this is a new background scan and basis maps were obtained, then compute the shim currents
            self.triggerComputeShimCurrents()
        t = threading.Thread(target=setMarker)
        t.daemon = True
        t.start()

        self.shimZero() # NOTE(rob): Hopefully this zeros quicker that the scans get set up...
        self.doBasisPairScan()
        # WAIT FOR THE BACKGROUND SCANS TO COMPLETE
    
    @requireExsiConnection
    @requireShimConnection
    @requireAssetCalibration
    def doLoopCalibrationScans(self):
        """Perform all the calibration scans for each loop in the shim system."""

        # Perform the background scans for the shim system.
        def setMarker():
            self.doLoopCalibrationScansMarker.setChecked(False)
            self.shimZero() # NOTE(rob): Hopefully this zeros quicker that the scans get set up...
            self.rawBasisB0maps = None
            self.exsiInstance.images_ready_event.clear()
            self.countScansCompleted(self.shimInstance.numLoops * 2)
            self.log("DEBUG: just finished all the calibration scans")
            self.doLoopCalibrationScansMarker.setChecked(True)
            self.computeBasisB0maps()
            # if this is a new background scan and basis maps were obtained, then compute the shim currents
            self.triggerComputeShimCurrents()
        t = threading.Thread(target=setMarker)
        t.daemon = True
        t.start()

        for i in range(self.shimInstance.numLoops):
            self.doCaliBasisPairScan(i)

    @requireShimConnection
    def shimSetAllCurrents(self):
        if not self.currentsComputedMarker.isChecked() or not self.currents:
            self.log("Debug: Need to perform background and loop calibration scans before setting currents.")
            msg = self.createMessageBox("Error: Background And Loop Cal Scans not Done",
                                        "Need to perform background and loop calibration scans before setting currents.", 
                                        "You could set them manually if you wish to.")
            msg.exec() 
            return # do nothing more
        self.setAllCurrentsMarker.setChecked(False)
        # require that currents have been computed, i.e. that background marker and loopcal marker are set
        # TODO(rob)
        for i in range(self.shimInstance.numLoops):
            self.shimSetCurrentManual(i%8, self.currents[i], i//8)
        self.setAllCurrentsMarker.setChecked(True)

    @requireExsiConnection
    @requireShimConnection
    @requireAssetCalibration
    def doShimmedScans(self):
        """ Perform another set of scans now that it is shimmed """
        if not self.setAllCurrentsMarker.isChecked():
                msg = self.createMessageBox("Note: Shim Process Not Performed",
                                            "If you want correct shims, click above buttons and redo.", "")
                msg.exec() 
        def setMarker():
            self.doShimmedScansMarker.setChecked(False)
            self.shimmedBackground = None
            self.countScansCompleted(2)
            self.doBackgroundScansMarker.setChecked(True)
            self.computeShimmedB0Map()
        t = threading.Thread(target=setMarker)
        t.daemon = True
        t.start()
 
        self.shimmedBackground = None
        self.doBasisPairScan()

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

    def computeBackgroundB0map(self):
        # assumes that you have just gotten background by doBasisPairScan
        b0maps = compute_b0maps(1, self.localExamRootDir)
        self.log(f"Debug: len background {len(b0maps)}")
        self.background = b0maps[0]

    def computeBasisB0maps(self):
        # assumes that you have just gotten background by doBasisPairScan
        self.rawBasisB0maps = compute_b0maps(self.shimInstance.numLoops, self.localExamRootDir)
        self.log(f"Debug: len rawbasisB0maps {len(self.rawBasisB0maps)}")
    
    def computeShimmedB0Map(self):
        b0maps = compute_b0maps(1, self.localExamRootDir)
        self.shimmedBackground = b0maps[0]

    def computeShimCurrents(self):
        # run whenever both backgroundB0Map and basisB0maps are computed or if one new one is obtained
        self.basisB0maps = subtractBackground(self.background, self.rawBasisB0maps)
        self.log(f"Debug: basisB0maps {len(self.basisB0maps)}")

        # TODO(rob): add the slider for slice index
        self.roiMask = createMask(self.background, self.basisB0maps, roi=None, sliceIndex=30)

        self.currents = solveCurrents(self.background, self.basisB0maps, self.roiMask)

        self.expShimmedBackground = self.background.copy()
        self.log(f"Debug: numloops {self.shimInstance.numLoops} and len currents {len(self.currents)} and len basisB0maps {len(self.basisB0maps)}")
        for i in range(self.shimInstance.numLoops):
            self.expShimmedBackground += self.currents[i] * self.basisB0maps[i]

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
        self.log(f"Debug: initiating transfer using rsync.")
        if self.gehcExamDataPath is None:
            self.setGehcExamDataPath()
        self.execRsyncCommand(self.gehcExamDataPath + '/*', self.localExamRootDir)

    def getLatestROIImage(self, stride=1, offset=0):
        self.log(f"Debug: local exam root {self.localExamRootDir}") 
        latestDCMDir = listSubDirs(self.localExamRootDir)[-1]
        self.log(f"debug: latest dcm dir {latestDCMDir}")
        res = extractBasicImageData(latestDCMDir, stride, offset)
        self.currentImageData, self.currentImageTE, self.currentImageOrientation = res
        self.log(f"Debug: showing image with this shape and type: {self.currentImageData.shape}, {self.currentImageData.dtype}")
        self.log(f"Debug: showing image with TE and scan name: {self.currentImageTE}, {self.currentImageOrientation}")
        roiSliceIndex = int(self.roiSliceEntry.text()) if self.roiSliceEntry.text() else 0
        self.updateROIImageDisplay(roiSliceIndex)

    # TODO(rob): remove these because they seem useless
    def execBashCommand(self, cmd):
        # Execute the bash command
        process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Wait for the command to complete
        stdout, stderr = process.communicate()
        
        # Check if the command was executed successfully
        if process.returncode == 0:
            return stdout.decode('utf-8')
        else:
            return f"Error: {stderr.decode('utf-8')}"

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
        self.log(f"DEBUG: self.backgroundset {self.background and self.background.shape}")
        self.log(f"DEBUG: self.rawb0mapsset {self.rawBasisB0maps and len(self.rawBasisB0maps)} {self.rawBasisB0maps and self.rawBasisB0maps[0].shape}")
        self.log(f"DEBUG: self.currents computed {self.currents}")
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
