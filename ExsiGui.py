from datetime import datetime
import sys, paramiko, subprocess, os, threading
import numpy as np
import json

import signal
from PyQt6.QtWidgets import QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget, QTextEdit, QLabel, QSlider, QHBoxLayout, QLineEdit, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsTextItem, QTabWidget, QCheckBox, QSizePolicy, QButtonGroup, QRadioButton
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QPixmap, QImage, QDoubleValidator, QIntValidator, QPainter, QPen, QBrush, QColor

# Import the custom client classes and util functions
from exsi_client import exsi
from shim_client import shim
from dicomUtils import *
from shimCompute import *
from utils import *

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
        
        # connect the clear queue commands so that they can be called from the other client
        self.shimInstance.clearExsiQueue = self.exsiInstance.clear_command_queue
        self.exsiInstance.clearShimQueue = self.shimInstance.clearCommandQueue

        # shim specific markers
        self.assetCalibrationDone = False
        self.autoPrescanDone = False
        self.obtainedBasisMaps = False
        self.computedShimCurrents = False

        # the results which are used to compute shim values
        self.shimSliceIndex = 20 # default slice index to shim at
        self.roiSliceIndex = 20
        self.roiDepth = None

        self.background = None
        self.expShimmedBackground = None
        self.shimmedBackground = None
        self.shimImages = [self.background, self.expShimmedBackground, self.shimmedBackground]
        self.shimImage = self.shimImages[0]

        self.shimStats = [None, None, None]

        self.rawBasisB0maps = []
        self.basisB0maps = []
        self.currents = None # currents for every single slice
        self.roiMask = None # for when the user draws in the desired 3d ROI
        self.finalMask = None # the actual mask used to compute any shimming

        # All the attributes for scan session that need to be None to start with.
        self.currentROIImageData = None
        self.backgroundDCMdir = None
        self.roiEditorEnabled = False
        self.roiSliderGranularity = 100

        self.currentImageTE = None
        self.currentImageOrientation = None


        self.gehcExamDataPath = None
        self.localExamRootDir = None

        self.slowButtons = []
        # Setup the GUI
        self.initUI()
        

    ##### GUI LAYOUT RELATED FUNCTIONS #####   

    def initUI(self):

        self.setWindowAndExamNumber()
        self.setGeometry(100, 100, 1200, 600)

        self.centralTabWidget = QTabWidget()
        self.setCentralWidget(self.centralTabWidget)
        self.centralTabWidget.currentChanged.connect(self.onTabSwitch)

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
        layout.addLayout(basicLayout)

        # Add imageLayout to the basicLayout
        imageLayout = QVBoxLayout()
        basicLayout.addLayout(imageLayout)

        # Slider for selecting slices
        self.roiSliceIndexSlider, self.roiSliceIndexEntry = self.addLabeledSliderAndEntry(imageLayout, "Slice Index (Int): ", QIntValidator(0, 0)) #TODO(rob): validate this after somehow....
        self.roiSliceIndexSlider.setValue(self.roiSliceIndex)
        self.roiSliceIndexEntry.setText(str(self.roiSliceIndex))
        self.roiSliceIndexSlider.valueChanged.connect(self.updateFromROISliceSlider)
        self.roiSliceIndexEntry.editingFinished.connect(self.updateFromSliceROIEntry)

        # Setup QGraphicsView for image display
        self.roiScene = QGraphicsScene()
        self.roiView = QGraphicsView(self.roiScene)
        self.roiView.setFixedSize(512, 512)  # Set a fixed size for the view
        self.roiView.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        imageLayout.addWidget(self.roiView, alignment=Qt.AlignmentFlag.AlignCenter)
        
        # roiPlaceholder text setup, and the actual pixmap item
        self.roiPlaceholderText = QGraphicsTextItem("Waiting for image data")
        self.roiPlaceholderText.setPos(50, 250)  # Position the text appropriately within the scene
        self.roiScene.addItem(self.roiPlaceholderText)
        self.roiPixmapItem = QGraphicsPixmapItem()
        self.roiScene.addItem(self.roiPixmapItem)
        self.roiPixmapItem.setZValue(1)  # Ensure pixmap item is above the roiPlaceholder text

        roiAdjustmentSliderLayout = QHBoxLayout()
        roiSizeSliders = QVBoxLayout()
        roiPositionSliders = QVBoxLayout()
        imageLayout.addLayout(roiAdjustmentSliderLayout)
        roiAdjustmentSliderLayout.addLayout(roiSizeSliders)
        roiAdjustmentSliderLayout.addLayout(roiPositionSliders)

        label = ["X", "Y", "Z"]
        self.roiSizeSliders = [None for _ in range(3)]
        self.roiPositionSliders = [None for _ in range(3)]
        for i in range(3):
            self.roiSizeSliders[i] = self.addLabeledSlider(roiSizeSliders, f"Size {label[i]}")
            self.roiSizeSliders[i].valueChanged.connect(self.visualizeROI)
            self.roiSizeSliders[i].setEnabled(False)
            self.roiPositionSliders[i] = self.addLabeledSlider(roiPositionSliders, f"Center {label[i]}")
            self.roiPositionSliders[i].valueChanged.connect(self.visualizeROI)
            self.roiPositionSliders[i].setEnabled(False)

        self.roiToggleButton = self.addButtonConnectedToFunction(imageLayout, "Enable ROI Editor", self.toggleROIEditor)

        # Controls and log layout
        controlsLayout = QVBoxLayout()
        basicLayout.addLayout(controlsLayout)
        self.setupExsiButtonsAndLog(controlsLayout)

        # Connect the log monitor
        self.exsiLogMonitorThread = LogMonitorThread(self.scannerLog)
        self.exsiLogMonitorThread.update_log.connect(self.updateExsiLogOutput)
        self.exsiLogMonitorThread.start()
        

    def setupExsiButtonsAndLog(self, layout):
        # create the buttons
        self.reconnectExsiButton = self.addButtonConnectedToFunction(layout, "Reconnect EXSI", self.exsiInstance.connectExsi)
        self.doCalibrationScanButton = self.addButtonConnectedToFunction(layout, "Do Calibration Scan", self.doCalibrationScan)
        self.doFgreScanButton = self.addButtonConnectedToFunction(layout, "Do FGRE Scan", self.doFgreScan)
        self.renderLatestDataButton = self.addButtonConnectedToFunction(layout, "Render Data", self.doGetAndSetROIImage)
        self.slowButtons += [self.doCalibrationScanButton, self.doFgreScanButton, self.renderLatestDataButton]

        # radio button group for selecting which roi view you want to see
        roiVizButtonWindow = QWidget()
        roiVizButtonLayout = QHBoxLayout()
        roiVizButtonWindow.setLayout(roiVizButtonLayout)
        self.roiVizButtonGroup = QButtonGroup(roiVizButtonWindow)
        layout.addWidget(roiVizButtonWindow)

        # Create the radio buttons
        roiLatestDataButton = QRadioButton("Latest Data")
        roiBackgroundButton = QRadioButton("Background")
        roiLatestDataButton.setChecked(True)  # Default to latest data
        roiBackgroundButton.setEnabled(False)  # Disable the background button for now
        roiVizButtonLayout.addWidget(roiLatestDataButton)
        roiVizButtonLayout.addWidget(roiBackgroundButton)
        self.roiVizButtonGroup.addButton(roiLatestDataButton, 0)
        self.roiVizButtonGroup.addButton(roiBackgroundButton, 1)
        self.roiVizButtonGroup.idClicked.connect(self.toggleROIBackgroundImage)

        self.exsiLogOutput = QTextEdit()
        self.exsiLogOutput.setReadOnly(True)
        self.exsiLogOutputLabel = QLabel("EXSI Log Output")

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
        shimVizButtonWindow.setLayout(shimVizButtonLayout)
        self.shimVizButtonGroup = QButtonGroup(shimVizButtonWindow)
        leftLayout.addWidget(shimVizButtonWindow)

        # Create the radio buttons
        shimVizButtonBackground = QRadioButton("Background")
        shimVizButtonEstBackground = QRadioButton("Estimated Shimmed Background")
        shimVizButtonShimmedBackground = QRadioButton("Actual Shimmed Background")
        shimVizButtonBackground.setChecked(True)  # Default to background

        # Add the radio buttons to the button group
        self.shimVizButtonGroup.addButton(shimVizButtonBackground, 0)
        shimVizButtonLayout.addWidget(shimVizButtonBackground)
        self.shimVizButtonGroup.addButton(shimVizButtonEstBackground, 1)
        shimVizButtonLayout.addWidget(shimVizButtonEstBackground)
        self.shimVizButtonGroup.addButton(shimVizButtonShimmedBackground, 2)
        shimVizButtonLayout.addWidget(shimVizButtonShimmedBackground)
        self.shimVizButtonGroup.idClicked.connect(self.toggleShimImage)

        # add another graphics scene visualizer
        # Setup QGraphicsView for image display
        self.shimScene = QGraphicsScene()
        self.shimView = QGraphicsView(self.shimScene)
        leftLayout.addWidget(self.shimView, alignment=Qt.AlignmentFlag.AlignCenter)
        self.shimView.setFixedSize(512, 512)  # Set a fixed size for the view
        self.shimView.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)

        # Placeholder text setup, and the actual pixmap item
        self.shimPlaceholderText = QGraphicsTextItem("Waiting for image data")
        self.shimPlaceholderText.setPos(50, 250)  # Position the text appropriately within the scene
        self.shimScene.addItem(self.shimPlaceholderText)
        self.shimPixmapItem = QGraphicsPixmapItem()
        self.shimScene.addItem(self.shimPixmapItem)
        #self.shimPixmapItem.setZValue(1)  # Ensure pixmap item is above the shimPlaceholder text

        # add the statistics output using another non editable textedit widget
        shimStatTextLabel = QLabel("Image Statistics")
        shimBackStatText = QTextEdit()
        shimBackStatText.setReadOnly(True)
        shimTestStatText = QTextEdit()
        shimTestStatText.setReadOnly(True)
        self.shimStatText = [shimBackStatText, shimTestStatText]
        statsLayout = QHBoxLayout()
        statsLayout.addWidget(shimBackStatText)
        statsLayout.addWidget(shimTestStatText)
        leftLayout.addWidget(shimStatTextLabel)
        leftLayout.addLayout(statsLayout)

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
        self.slowButtons += [self.shimCalChannelsButton, self.shimZeroButton, self.shimGetCurrentsButton, self.shimManualSetCurrentButton]

        # ACTUAL SHIM OPERATIONS START
        # Just add the rest of the things to the vertical layout.
        # add a label and select for the slice index
        self.shimSliceIndexSlider, self.shimSliceIndexEntry = self.addLabeledSliderAndEntry(rightLayout, "Slice Index (Int): ", QIntValidator(0, 2147483647)) #TODO(rob): validate this after somehow....
        self.shimSliceIndexSlider.valueChanged.connect(self.updateFromShimSliceIndexSlider)
        self.shimSliceIndexEntry.editingFinished.connect(self.updateFromShimSliceIndexEntry)
        self.shimSliceIndexSlider.setValue(self.shimSliceIndex)
        self.shimSliceIndexEntry.setText(str(self.shimSliceIndex))
        self.shimSliceIndexSlider.setEnabled(False)
        self.shimSliceIndexEntry.setEnabled(False)

        recomputeLayout = QHBoxLayout()
        self.recomputeCurrentsButton, self.withLinGradMarker = self.addButtonWithFuncAndMarker(recomputeLayout, "Shim: Recompute Currents", self.recomputeCurrents, "Linear Gradients?")
        self.slowButtons += [self.recomputeCurrentsButton]
        self.currentsDisplay = QLineEdit()
        self.currentsDisplay.setReadOnly(True)
        recomputeLayout.addWidget(self.currentsDisplay)
        rightLayout.addLayout(recomputeLayout)

        self.doShimProcedureLabel = QLabel("SHIM OPERATIONS")
        rightLayout.addWidget(self.doShimProcedureLabel)

        # macro for obtaining background scans
        self.doBackgroundScansButton, self.doBackgroundScansMarker = self.addButtonWithFuncAndMarker(rightLayout, "Shim: Perform Background B0map Scans", self.doBackgroundScans)
        self.doLoopCalibrationScansButton, self.doLoopCalibrationScansMarker = self.addButtonWithFuncAndMarker(rightLayout, "Shim: Perform Loop Calibration B0map Scans", self.doLoopCalibrationScans)
        setAllCurrentsLayout = QHBoxLayout() # need a checkbox in front of the set all currents button to show that the currents have been computed
        rightLayout.addLayout(setAllCurrentsLayout)
        self.currentsComputedMarker = QCheckBox("Currents Computed?")
        self.currentsComputedMarker.setEnabled(False)
        self.currentsComputedMarker.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        setAllCurrentsLayout.addWidget(self.currentsComputedMarker)
        self.setAllCurrentsButton, self.setAllCurrentsMarker = self.addButtonWithFuncAndMarker(setAllCurrentsLayout, "Shim: Set All Computed Currents", self.shimSetAllCurrents)
        self.doShimmedScansButton, self.doShimmedScansMarker = self.addButtonWithFuncAndMarker(rightLayout, "Shim: Perform Shimmed Eval Scans", self.doShimmedScans)
        self.slowButtons += [self.doBackgroundScansButton, self.doLoopCalibrationScansButton, self.setAllCurrentsButton, self.doShimmedScansButton]

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

    def addLabeledSlider(self, layout, labelStr, orientation=Qt.Orientation.Horizontal):
        slider = QSlider(orientation)
        label = QLabel(labelStr)
        labelEntryLayout = QHBoxLayout()
        labelEntryLayout.addWidget(label)
        labelEntryLayout.addWidget(slider)
        slider.setMinimum(0)
        slider.setMaximum(self.roiSliderGranularity)
        slider.setValue((round(self.roiSliderGranularity)//2))
        layout.addLayout(labelEntryLayout)
        return slider

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
    
    def addButtonWithFuncAndMarker(self, layout, buttonName, function, markerName="Done?"):
        hlayout = QHBoxLayout()
        layout.addLayout(hlayout)
        marker = QCheckBox(markerName)
        marker.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if markerName == "Done?":
            marker.setEnabled(False)
            button = self.addButtonConnectedToFunction(hlayout, buttonName, function)
            hlayout.addWidget(marker)
        else:
            hlayout.addWidget(marker)
            button = self.addButtonConnectedToFunction(hlayout, buttonName, function)
        return button, marker

    ##### GRAPHICS FUNCTION DEFINITIONS #####   

    def setViewImage(self, qImage, roi=True):
        pixmap = QPixmap.fromImage(qImage)
        if roi:
            self.roiView.viewport().setVisible(True)
            self.roiPixmapItem.setPixmap(pixmap)
            self.roiScene.setSceneRect(self.roiPixmapItem.boundingRect())  # Adjust scene size to the pixmap's bounding rect
            self.roiView.fitInView(self.roiPixmapItem, Qt.AspectRatioMode.KeepAspectRatio)  # Fit the view to the item
            self.roiPlaceholderText.setVisible(False)
            self.roiView.viewport().update()  # Force the viewport to update
        else:
            self.shimView.viewport().setVisible(True)
            self.shimPixmapItem.setPixmap(pixmap)
            self.shimScene.setSceneRect(self.shimPixmapItem.boundingRect())  # Adjust scene size to the pixmap's bounding rect
            self.shimView.fitInView(self.shimPixmapItem, Qt.AspectRatioMode.KeepAspectRatio)  # Fit the view to the item
            self.shimPlaceholderText.setVisible(False)
            self.shimView.viewport().update()  # Force the viewport to update
    
    def updateROIImageDisplay(self):
        if self.currentROIImageData is not None:
            self.validateROISliceIndexControls(self.roiSliceIndex)
            # Extract the slice and normalize it
            if self.roiVizButtonGroup.checkedId() == 1:
                sliceData = np.ascontiguousarray(self.currentROIImageData[:,self.roiSliceIndex,:]).astype(float)
            else:
                sliceData = np.ascontiguousarray(self.currentROIImageData[self.roiSliceIndex]).astype(float)
            normalizedData = (sliceData - sliceData.min()) / (sliceData.max() - sliceData.min()) * 255
            displayData = normalizedData.astype(np.uint8)  # Convert to uint8
            # Create a 3-channel image from grayscale data
            rgbData = np.stack((displayData,)*3, axis=-1)
            height, width, _ = rgbData.shape
            bytesPerLine = rgbData.strides[0] 
            self.roiQImage = QImage(rgbData.data, width, height, bytesPerLine, QImage.Format.Format_RGB888)
            if self.roiQImage.isNull():
                self.log("Debug: Failed to create QImage")
            else:
                if self.roiEditorEnabled:
                    self.visualizeROI()
                else:
                    self.setViewImage(self.roiQImage)
        else:
            self.roiPlaceholderText.setVisible(True)

    def validateROISliceIndexControls(self, value):
        if self.currentROIImageData is not None:
            # Update the slider range based on the new data
            if self.roiVizButtonGroup.checkedId() == 1:
                self.roiDepth = self.currentROIImageData.shape[1]
            else:
                self.roiDepth = self.currentROIImageData.shape[0]
            self.roiSliceIndexSlider.setMinimum(0)
            self.roiSliceIndexSlider.setMaximum(self.roiDepth - 1)
            self.roiSliceIndexEntry.setValidator(QIntValidator(0, self.roiDepth - 1))

            # If roiSliceIndex is None or out of new bounds, default to first slice
            if value is None:
                self.log("DEBUG: Invalid slice index, defaulting to 0")
                self.roiSliceIndex = 0
            elif value >= self.roiDepth:
                self.log("DEBUG: Invalid slice index, defaulting to roi.depth - 1")
                self.roiSliceIndex = self.roiDepth - 1
            else:
                self.roiSliceIndex = value
        else:
            self.roiSliceIndex = value
    
    def visualizeROI(self):
        # TODO add mote kinds of shapes and make this more scalable 

        sizes = []
        center = []
        for i in range(3):
            sizes.append(self.roiSizeSliders[i].value() / 100)
            center.append(self.roiPositionSliders[i].value() / 100)
        
        self.XSizeEllipsoid = round((self.roiQImage.width() // 2) * sizes[0])
        self.YSizeEllipsoid = round((self.roiQImage.height() // 2) * sizes[1])
        self.ZSizeEllipsoid = round((self.roiDepth // 2) * sizes[2])
        self.XCenterEllipsoid = round(self.roiQImage.width() * center[0])
        self.YCenterEllipsoid = round(self.roiQImage.height() * center[1])
        self.ZCenterEllipsoid = round(self.roiDepth * center[2])

        self.log(f"DEBUG: Drawing oval on slice {self.roiSliceIndex}")
        self.log(f"DEBUG: XCenter: {self.XCenterEllipsoid}, YCenter: {self.YCenterEllipsoid}, ZCenter: {self.ZCenterEllipsoid}")
        self.log(f"DEBUG: XSize: {self.XSizeEllipsoid}, YSize: {self.YSizeEllipsoid}, ZSize: {self.ZSizeEllipsoid}")

        # based on self.roiSliceIndex, we can determine what percent of SizeEllipsoid to use for the oval slice
        offsetFromDepthCenter = abs(self.roiSliceIndex - self.ZCenterEllipsoid)
        self.log(f"DEBUG: Offset from depth center: {offsetFromDepthCenter}")
        if offsetFromDepthCenter <= self.ZSizeEllipsoid:
            factor = (1 - (offsetFromDepthCenter**2 / self.ZSizeEllipsoid**2))
            self.log(f"DEBUG: Factor for oval: {factor}")
            width_oval = round(np.sqrt(self.XSizeEllipsoid**2 * factor))
            height_oval = round(np.sqrt(self.YSizeEllipsoid**2 * factor))
            self.log(f"DEBUG: Width: {width_oval}, Height: {height_oval}")

            # visualize an oval overlayed on top of the current self.roiQImage
            # Create a QPainter object and begin painting on the QImage
            qImage = self.roiQImage.copy()
            painter = QPainter(qImage)
            # Set the pen color to red and the brush to a transparent red
            painter.setPen(QPen(QBrush(QColor(255, 0, 0, 255)), 1))

            # Draw an oval on the image. Adjust the parameters as needed for your specific use case
            painter.drawEllipse(QPoint(self.XCenterEllipsoid, self.YCenterEllipsoid), width_oval, height_oval)
            # End painting
            painter.end()

            # Now you can use the QImage as before
            self.setViewImage(qImage)
        else:
            self.setViewImage(self.roiQImage)

    def updateFromSliceROIEntry(self):
        # Update the display based on the manual entry in QLineEdit
        index = int(self.roiSliceIndexEntry.text()) if self.roiSliceIndexEntry.text() else 0
        self.validateROISliceIndexControls(index)
        self.roiSliceIndexSlider.setValue(self.roiSliceIndex)
        self.updateROIImageDisplay()

    def updateFromROISliceSlider(self, value):
        # Directly update the line edit when the slider value changes
        self.validateROISliceIndexControls(value)
        self.roiSliceIndexEntry.setText(str(self.roiSliceIndex))
        self.updateROIImageDisplay()

    # TODO(rob) should make this a single function since it is basically a copy of the other one
    def updateShimImageAndStats(self):
        self.log(f"DEBUG selected shim image: {self.shimVizButtonGroup.checkedId()}")
        select = self.shimVizButtonGroup.checkedId()
        self.shimImage = self.shimImages[select]
        if self.shimImage is not None:
            self.shimImage = self.shimImage if select == 0 else self.shimImage[self.shimSliceIndex]
            if self.shimImage is not None:
                sliceData = np.ascontiguousarray(self.shimImage[:,self.shimSliceIndex,:]).astype(float)
                # want this normalization to be constant for every slice and for every kind of image...
                normalizedData = (sliceData - self.shimImageValMin) / (self.shimImageValMax - self.shimImageValMin) * 255
                displayData = normalizedData.astype(np.uint8)  # Convert to uint8
                height, width = displayData.shape
                bytesPerLine = displayData.strides[0] 
                qImage = QImage(displayData.data, width, height, bytesPerLine, QImage.Format.Format_Grayscale8)
                if qImage.isNull():
                    self.log("Debug: Failed to create QImage")
                else:
                    self.shimView.viewport().setVisible(True)
                    pixmap = QPixmap.fromImage(qImage)
                    self.shimPixmapItem.setPixmap(pixmap)
                    self.shimScene.setSceneRect(self.shimPixmapItem.boundingRect())  # Adjust scene size to the pixmap's bounding rect
                    self.shimView.fitInView(self.shimPixmapItem, Qt.AspectRatioMode.KeepAspectRatio)  # Fit the view to the item
                    self.shimPlaceholderText.setVisible(False)
                    self.shimView.viewport().update()  # Force the viewport to update

                # show the background stat always:
                if self.shimStats[0] is not None:
                    text = self.shimStats[select][self.shimSliceIndex]
                    if text is None:
                        text = "no stats available"
                else:
                    text = "No stats available"
                self.shimStatText[0].setText("Background " + text)

                prefixs = ["Est. ", "Actual "]
                if select > 0:
                    if self.shimStats[select] is not None:
                        text = self.shimStats[select][self.shimSliceIndex]
                        if text is None:
                            text = "no stats available"
                    else:
                        text = "No stats available"                
                    text = prefixs[select] + text
                    self.shimStatText[select].setText(text)

                # if currents are available
                if self.currents is not None:
                    if self.currents[self.shimSliceIndex] is not None:
                        text = ""
                        for i in range(self.shimInstance.numLoops):
                            text += f"{i}:{self.currents[self.shimSliceIndex][i]:.3f}|"
                    else:
                        text = "No currents available "
                    self.currentsDisplay.setText(text[:-1])
                else:
                    self.currentsDisplay.setText("No currents available")
                return 
        self.shimView.viewport().setVisible(False)
        self.shimPlaceholderText.setVisible(True)
    
    def validateShimSliceIndexControls(self, value):
        if self.shimImages[0] is not None:
            # update depth to consider which orientation is selected. in which case it wont be 0 here if not coronal
            depth = self.shimImages[0].shape[1]
            self.shimSliceIndexEntry.setValidator(QIntValidator(0, depth - 1))
            self.shimSliceIndexSlider.setMinimum(0)
            self.shimSliceIndexSlider.setMaximum(depth - 1)
            # If shimSliceIndex is None or out of new bounds, default to first slice
            if value is None or value >= depth:
                self.log("DEBUG: Invalid slice index, defaulting to 0")
                self.shimSliceIndex = 0
            else:
                self.shimSliceIndex = value

        self.shimImageValMax, self.shimImageValMin = -np.inf, np.inf
        for i in range(3):
            if self.shimImages[i] is not None:
                if i == 0:
                    self.shimImageValMax = max(np.nanmax(self.shimImages[i]), self.shimImageValMax)
                    self.shimImageValMin = min(np.nanmin(self.shimImages[i]), self.shimImageValMin)
                else:
                    for j in range(self.shimImages[0].shape[1]):
                        if self.shimImages[i][j] is not None:
                            self.shimImageValMax = max(np.nanmax(self.shimImages[i][j]), self.shimImageValMax)
                            self.shimImageValMin = min(np.nanmin(self.shimImages[i][j]), self.shimImageValMin)

    def updateFromShimSliceIndexEntry(self):
        index = int(self.shimSliceIndexEntry.text()) if self.shimSliceIndexEntry.text() else 0
        self.shimSliceIndexSlider.setValue(index)
        self.shimSliceIndex = index
        self.updateShimImageAndStats()

    def updateFromShimSliceIndexSlider(self, value):
        # Directly update the line edit when the slider value changes
        self.shimSliceIndex = value
        self.shimSliceIndexEntry.setText(str(self.shimSliceIndex))
        self.updateShimImageAndStats()

    def updateExsiLogOutput(self, text):
        self.exsiLogOutput.append(text)

    def updateShimLogOutput(self, text):
        self.shimLogOutput.append(text)

    def toggleShimImage(self, id):
        self.log(f"DEBUG: Toggling shim image to {id}")
        self.shimImage = self.shimImages[id]
        self.updateShimImageAndStats()
    
    def toggleROIBackgroundImage(self):
        pass

    def toggleROIEditor(self):
        if self.roiVizButtonGroup.checkedId() == 1:
            if self.roiEditorEnabled:
                self.roiEditorEnabled = False
                self.roiToggleButton.setText("Enable ROI Editor")
            else:
                for i in range(3):
                    self.roiSizeSliders[i].setEnabled(True)
                    self.roiPositionSliders[i].setEnabled(True)
                self.roiEditorEnabled = True
                self.roiToggleButton.setText("Disable ROI Editor")
            self.updateROIImageDisplay()
    
    ##### HELPER FUNCTIONS FOR EXSI CONTROL BUTTONS !!!! NO BUTTON SHOULD MAP TO THIS #####
    
    def queueBasisPairScanDetails(self):
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
            if not self.autoPrescanDone and not self.debugging:
                self.sendPrescan(auto=True)
                self.autoPrescanDone = True
            else:
                self.sendPrescan(auto=False)
            self.sendScan()

    def queueLoadWithCaliCurrentSet(self, channelNum):
        # when the exsiclient gets this specific command, it will know to dispatch both the loadProtocol 
        # command and also a Zero Current and setCurrent to channelNum with calibration current of 1.0
        self.sendLoadProtocol(f"ConformalShimCalibration3 | {channelNum} 1.0")

    def queueBasisPairScan(self):
        # Basic basis pair scan. should be used to scan the background
        self.sendLoadProtocol("ConformalShimCalibration3")
        self.queueBasisPairScanDetails()

    def queueCaliBasisPairScan(self, channelNum):
        self.queueLoadWithCaliCurrentSet(channelNum)
        self.queueBasisPairScanDetails()

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
        else:
            self.computeMask(self.rawBasisB0maps)
        self.evaluateShimImages()
    
    def saveROIMask(self):
        # make a mask the same shape as self.shimImages[0] based on the ellipsoid parameters
        if self.roiEditorEnabled:
            self.log(f"DEBUG: Saving ROI mask")
            mask = np.zeros_like(self.shimImages[0], dtype=bool)
            for z in range(mask.shape[1]):
                for y in range(mask.shape[0]):
                    for x in range(mask.shape[2]):
                        if ((x - self.XCenterEllipsoid)**2 / self.XSizeEllipsoid**2 +
                            (y - self.YCenterEllipsoid)**2 / self.YSizeEllipsoid**2 +
                            (z - self.ZCenterEllipsoid)**2 / self.ZSizeEllipsoid**2) <= 1:
                            # If it is, set the corresponding element in the mask to True
                            mask[z, y, x] = True
            self.roiMask = mask
            # apply the mask to any of the shimImages that may exist
            self.log(f"DEBUG: Mask shape: {self.roiMask.shape}, applying to shimImages")
            # TODO(rob): make it so that this is not overriding the data, but rather just a copy for visualization
            #       need to do this bc when the mask is changed, the og data needs to be remasked
            if self.shimImages[0] is not None:
                self.shimImages[0] = np.where(self.roiMask, self.shimImages[0], np.nan)
                self.log(f"DEBUG: applied to background")
            if self.shimImages[1] is not None:
                for i in range(self.shimImages[0].shape[1]):
                    self.log(f"DEBUG: applied to basis map {i}")
                    self.shimImages[1][i] = np.where(self.roiMask, self.shimImages[1][i], np.nan)
        else:
            self.roiMask = None
        

    def onTabSwitch(self, index):
        self.log(f"DEBUG: Switched to tab {index}")
        if index == 1:
            self.saveROIMask()
            self.updateShimImageAndStats()

                        
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

    #### More macro type button functions. These are slow, so disable other buttons while they are running, but unblock the rest of the gui ####
    
    @disableSlowButtonsTillDone
    def getAndSetROIImageWork(self, trigger):
        if self.roiVizButtonGroup.checkedId() == 1:
            self.log("Debug: Getting background image") 
            self.getROIBackgound()
        else:
            self.log("Debug: Getting latest image") 
            self.transferScanData()
            if os.path.exists(self.localExamRootDir):
                self.getLatestData(stride=1)
            else:
                self.log("Debug: local directory has not been made yet...")
        trigger.finished.emit()
    @requireExsiConnection
    def doGetAndSetROIImage(self):
        work = Trigger()
        work.finished.connect(self.updateROIImageDisplay)
        kickoff_thread(self.getAndSetROIImageWork, args=(work,))


    @disableSlowButtonsTillDone
    def calibrationScanWork(self, trigger):
        self.sendLoadProtocol("ConformalShimCalibration4")
        self.sendSelTask()
        self.sendActTask()
        self.sendPatientTable()
        self.sendScan()
        # TODO(rob): make this a part of the decorator function to grey out related buttons
        if self.exsiInstance.images_ready_event.wait(timeout=120):
            self.assetCalibrationDone = True
            self.exsiInstance.images_ready_event.clear()
        trigger.finished.emit()
    @requireExsiConnection
    def doCalibrationScan(self):
        # dont need to do the assetCalibration scan more than once
        if not self.exsiInstance or self.assetCalibrationDone:
            return
        trigger = Trigger()
        trigger.finished.connect(self.updateROIImageDisplay)
        kickoff_thread(self.calibrationScanWork, args=(trigger,))
    

    @disableSlowButtonsTillDone
    def fgreScanWork(self, trigger):
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
            self.getLatestData(stride=1)
        trigger.finished.emit()
    @requireExsiConnection
    @requireAssetCalibration
    def doFgreScan(self):
        if not self.exsiInstance:
            return
        trigger = Trigger()
        trigger.finished.connect(self.updateROIImageDisplay)
        kickoff_thread(self.fgreScanWork, args=(trigger,))
    
    @disableSlowButtonsTillDone
    def recomputeCurrents(self):
        self.triggerComputeShimCurrents()
        self.updateShimImageAndStats()

    @disableSlowButtonsTillDone
    def waitBackgroundScan(self, trigger):
        self.doBackgroundScansMarker.setChecked(False)
        self.shimZero() # NOTE(rob): Hopefully this zeros quicker that the scans get set up...
        self.shimImages[0] = None
        self.exsiInstance.images_ready_event.clear()
        self.countScansCompleted(2)
        self.doBackgroundScansMarker.setChecked(True)
        self.roiVizButtonGroup.buttons()[1].setEnabled(True)
        self.transferScanData()
        self.log("DEBUG: just finished all the background scans")
        self.computeBackgroundB0map()
        # if this is a new background scan and basis maps were obtained, then compute the shim currents
        self.triggerComputeShimCurrents()
        trigger.finished.emit()
    @requireExsiConnection
    @requireShimConnection
    @requireAssetCalibration
    def doBackgroundScans(self):
        # Perform the background scans for the shim system.
        trigger = Trigger()
        def updateVals():
            self.shimSliceIndexSlider.setEnabled(True)
            self.shimSliceIndexEntry.setEnabled(True)
            self.updateShimImageAndStats()
        trigger.finished.connect(updateVals)
        kickoff_thread(self.waitBackgroundScan, args=(trigger,))
        self.queueBasisPairScan()
    

    @disableSlowButtonsTillDone
    def waitLoopCalibrtationScan(self, trigger):
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
        trigger.finished.emit()
    @requireExsiConnection
    @requireShimConnection
    @requireAssetCalibration
    def doLoopCalibrationScans(self):
        """Perform all the calibration scans for each loop in the shim system."""
        trigger = Trigger()
        trigger.finished.connect(self.updateShimImageAndStats)
        kickoff_thread(self.waitLoopCalibrtationScan, args=(trigger,))
        def queueAll():
            for i in range(self.shimInstance.numLoops):
                self.queueCaliBasisPairScan(i)
        kickoff_thread(queueAll)


    @requireShimConnection
    def shimSetAllCurrents(self):
        if not self.currentsComputedMarker.isChecked() or not self.currents:
            self.log("Debug: Need to perform background and loop calibration scans before setting currents.")
            msg = createMessageBox("Error: Background And Loop Cal Scans not Done",
                                   "Need to perform background and loop calibration scans before setting currents.", 
                                   "You could set them manually if you wish to.")
            msg.exec() 
            return # do nothing more
        self.setAllCurrentsMarker.setChecked(False)
        # require that currents have been computed, i.e. that background marker and loopcal marker are set
        # TODO(rob)
        if self.currents[self.shimSliceIndex] is not None:
            for i in range(self.shimInstance.numLoops):
                self.shimSetCurrentManual(i%8, self.currents[self.shimSliceIndex][i], i//8)
        self.setAllCurrentsMarker.setChecked(True)


    @disableSlowButtonsTillDone
    def waitShimmedScans(self, trigger):
        self.doShimmedScansMarker.setChecked(False)
        self.shimImages[2] = None
        self.exsiInstance.images_ready_event.clear()
        self.countScansCompleted(2)
        self.doShimmedScansMarker.setChecked(True)
        self.computeShimmedB0Map()
        self.evaluateShimImages()
        trigger.finished.emit()
    @requireExsiConnection
    @requireShimConnection
    @requireAssetCalibration
    def doShimmedScans(self):
        """ Perform another set of scans now that it is shimmed """
        if not self.setAllCurrentsMarker.isChecked():
                msg = createMessageBox("Note: Shim Process Not Performed",
                                       "If you want correct shims, click above buttons and redo.", "")
                msg.exec() 
        trigger = Trigger()
        trigger.finished.connect(self.updateShimImageAndStats)
        kickoff_thread(self.waitShimmedScans, args=(trigger,))
        self.queueBasisPairScan()

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

    def computeMask(self, bases):
        """compute the mask for the shim images."""
        # TODO(rob) add in the user set ROI
        self.finalMask = []
        for i in range(self.shimImages[0].shape[1]):
            if self.roiMask is not None:
                self.finalMask.append(createMask(self.shimImages[0], bases, roi=self.roiMask[i], sliceIndex=i))
            else:
                self.finalMask.append(createMask(self.shimImages[0], bases, roi=None, sliceIndex=i))
    
    def evaluateShimImages(self):
        """evaluate the shim images and store the stats in the stats array."""
        for i in range(3):
            if self.shimStats[i] is None:
                self.shimStats[i] = [None for _ in range(self.shimImages[0].shape[1])]
            if self.shimImages[i] is not None:
                for j in range(self.shimImages[0].shape[1]):
                    if i == 0:
                        statsstr, std_og, mean_og, median_og = evaluate(self.shimImages[i][self.finalMask[j]], self.debugging)
                        self.shimStats[i][j] = statsstr
                    elif self.shimImages[i][j] is not None:
                        statsstr, std_og, mean_og, median_og = evaluate(self.shimImages[i][j][self.finalMask[j]], self.debugging)
                        self.shimStats[i][j] = statsstr

    def computeBackgroundB0map(self):
        # assumes that you have just gotten background by queueBasisPairScan
        b0maps = compute_b0maps(1, self.localExamRootDir)
        self.backgroundDCMdir = listSubDirs(self.localExamRootDir)[-1]
        self.shimImages[0] = b0maps[0]
        self.validateShimSliceIndexControls(self.shimSliceIndex)

    def computeBasisB0maps(self):
        # assumes that you have just gotten background by queueBasisPairScan
        self.rawBasisB0maps = compute_b0maps(self.shimInstance.numLoops, self.localExamRootDir)
    
    def computeShimCurrents(self):
        # run whenever both backgroundB0Map and basisB0maps are computed or if one new one is obtained
        self.basisB0maps = subtractBackground(self.shimImages[0], self.rawBasisB0maps)
        self.computeMask(self.basisB0maps)

        self.currents = [None for _ in range(self.shimImages[0].shape[1])]
        for i in range(self.shimImages[0].shape[1]):
            self.currents[i] = solveCurrents(self.shimImages[0], self.basisB0maps, self.finalMask[i], withLinGrad=self.withLinGradMarker.isChecked(), debug=self.debugging)

        # if all currents are none
        if not all([c is None for c in self.currents]):
            self.shimImages[1] = [self.shimImages[0].copy() for _ in range(self.shimImages[0].shape[1])]
            for i in range(self.shimInstance.numLoops):
                for j in range(self.shimImages[0].shape[1]):
                    if self.currents[j] is not None:
                        self.shimImages[1][j] += self.currents[j][i] * self.basisB0maps[i]
                    else:
                        self.shimImages[1][j] = None
            self.currentsComputedMarker.setChecked(True)
            self.validateShimSliceIndexControls(self.shimSliceIndex)
        else:
            # TODO(rob): can't make a message box in another thread i think
            # make a message box saying currents could not compute
            # msg = createMessageBox("Error: Could Not Solve For Currents",
            #                        "Might be that they aren't getting set and least squares becomes lowrank.", "")
            # msg.exec()
            self.log("Error: Could not solve for currents. Look at error hopefully in output")

    def computeShimmedB0Map(self):
        b0maps = compute_b0maps(1, self.localExamRootDir)
        if self.shimImages[2] is None:
            self.shimImages[2] = [None for _ in range(self.shimImages[0].shape[1])]
        self.shimImages[2][self.shimSliceIndex] = b0maps[0]
        self.validateShimSliceIndexControls(self.shimSliceIndex)

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

    def getLatestData(self, stride=1, offset=0):
        latestDCMDir = listSubDirs(self.localExamRootDir)[-1]
        res = extractBasicImageData(latestDCMDir, stride, offset)
        self.currentROIImageData, self.currentImageTE, self.currentImageOrientation = res
    
    def getROIBackgound(self):
        self.log('Debug: extracting the background mag image')
        res = extractBasicImageData(self.backgroundDCMdir, stride=3, offset=0)
        self.log('Debug: done extracting the background mag image')
        self.currentROIImageData = res[0]


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
        self.log(f"DEBUG: self.background set {self.shimImages[0].shape if self.shimImages[0] else None}")
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
