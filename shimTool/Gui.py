"""
This module contains the GUI for the application.
Includes both ui instantiation, as well as the logic for populating UI with data
"""

import pickle, sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QBoxLayout, QVBoxLayout, QWidget, QTextEdit, QLabel, QSlider, QHBoxLayout, QLineEdit, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsTextItem, QTabWidget, QCheckBox, QSizePolicy, QButtonGroup, QRadioButton, QGraphicsItem
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QImage, QDoubleValidator, QIntValidator, QPainter, QPen, QBrush, QColor, QFontMetrics

from shimTool.Tool import Tool
from shimTool.guiUtils import *
from shimTool.utils import *

class Gui(QMainWindow):
    """
    The ExsiGui class represents the main GUI window for controlling the Exsi system.
    It provides functionality for connecting to the scanner client, the Shim client,
    and performing various operations related to calibration, scanning, and shimming.
    """

    def __init__(self, config, debugging):
        self.app = QApplication(sys.argv)
        super().__init__()
        # Load the stylesheet
        # get the location of this file and find a sibling file called styles.qss
        with open(os.path.join(os.path.dirname(__file__), "styles.qss"), "r") as file:
            self.app.setStyleSheet(file.read())

        # ----- Tool Variables ----- # 
        self.debugging = debugging # tick this only when you continue to be devving...
        self.shimTool = Tool(config, self.debugging)


        # ----- GUI Properties that dont change ----- #
        self.latestStateSavePath = os.path.join("toolStates", "guiLatestState.pkl")
        # array of buttons that need to be disabled during slow operations in other threads
        self.slowButtons = []

        # how fine scrolling you want the ROI sliders to be. 100 is more than enough typically...
        self.roiSliderGranularity = 100

        # ----- GUI Properties that act as state, in addition to all the gui features that hold state ----- #
        # array of views. so that we can generalize update function for all views
        # should be roi view, shim view, and then basis view
        self.views = []
        self.viewDataSlice = np.array([np.nan for _ in range(3)], dtype=object) # three sets of 2D Slice Data that is actually visualized

        # the value range for each view
        self.viewMaxAbs = [0 for _ in range(3)]

        # start building the state vector for the GUI. These are all the essential data structures that are necessary to reload the app
        self.state = {
            "checkboxes": {}
        }

        # ----- GUI Initialization ----- #
        self.initUI()
        self.run()
        
    def run(self):
        self.show()

        # wait for exsi connected to update the name of the GUI application, the tab, and to create the exam data directory
        def waitForExSIConnectedReadyEvent():
            if not self.shimTool.exsiInstance.connected_ready_event.is_set():
                self.shimTool.exsiInstance.connected_ready_event.wait()
            self.shimTool.localExamRootDir = os.path.join(self.shimTool.config['rootDir'], "data", self.shimTool.exsiInstance.examNumber)
            if not os.path.exists(self.shimTool.localExamRootDir):
                os.makedirs(self.shimTool.localExamRootDir)
            self.setWindowAndExamNumber(self.shimTool.exsiInstance.examNumber, self.shimTool.exsiInstance.patientName)
            self.renameTab(self.exsiTab, "ExSI Control")
        # wait for the shim drivers connected to update the tab name
        def waitForShimConnectedEvent():
            if not self.shimTool.shimInstance.connectedEvent.is_set():
                self.shimTool.shimInstance.connectedEvent.wait()
            self.renameTab(self.shimmingTab, "Shim Control")
    
        # let us hope that there isn't some race condition here...
        kickoff_thread(waitForExSIConnectedReadyEvent)
        kickoff_thread(waitForShimConnectedEvent)

        # start the PyQt event loop
        sys.exit(self.app.exec())


    ##### GUI LAYOUT RELATED FUNCTIONS #####   

    def initUI(self):
        """Initialize the GUI layout and components."""
        
        # set the window title and size
        # this is the encapsulating stuff for every other aspect of the gui
        self.setGeometry(100, 100, 1500, 800)
        self.setWindowAndExamNumber() # set the window title of the application

        mainWidget = QWidget()
        mainLayout = QVBoxLayout() 
        mainWidget.setLayout(mainLayout)
        self.setCentralWidget(mainWidget)

        # add two buttons above the tabs, for saving state and loading state

        if (self.debugging):
            stateButtonLayout = QHBoxLayout()
            mainLayout.addLayout(stateButtonLayout)
            self.saveStateButton = addButtonConnectedToFunction(stateButtonLayout, "DEBUG ONLY: Save Current State", self.saveState)
            self.loadStateButton = addButtonConnectedToFunction(stateButtonLayout, "DEBUG ONLY: Load Latest State", self.loadState)

        # create and configure the main tabs / views of the GUI
        self.centralTabWidget = QTabWidget()
        self.centralTabWidget.currentChanged.connect(self.onTabSwitch)
        mainLayout.addWidget(self.centralTabWidget)
        
        # for the basic tab, viewing normal images, adding ROI, and also for calibration
        self.exsiTab = QWidget()
        basicLayout = QVBoxLayout()
        self.setupBasicTabLayout(basicLayout) # main exsi tab layout setup
        self.exsiTab.setLayout(basicLayout)

        # for shim tab. viewing b0maps and shimming
        self.shimmingTab = QWidget()
        shimmingLayout = QVBoxLayout()
        self.setupShimmingTabLayout(shimmingLayout) # main shim tab layout setup
        self.shimmingTab.setLayout(shimmingLayout)

        # for basis tab. viewing basis b0images, and also seeing more performance metrics
        self.basisTab = QWidget()
        basisLayout = QVBoxLayout()
        self.setup3rdTabLayout(basisLayout) # main basis tab layout setup
        self.basisTab.setLayout(basisLayout)

        # add the tabs to the main window
        # NOTE: if you change the order of the tabs, will need to adjust the onTabSwitch function
        self.centralTabWidget.addTab(self.exsiTab, "EXSI Control [Not Connected]")
        self.centralTabWidget.addTab(self.shimmingTab, "SHIM Control [Not Connected]")
        self.centralTabWidget.addTab(self.basisTab, "Basis/Performance Visualization")

        # Connect the log monitor
        self.exsiLogMonitorThread = LogMonitorThread(self.shimTool.scannerLog)
        self.exsiLogMonitorThread.update_log.connect(partial(self.updateLogOutput, self.exsiLogOutput))
        self.exsiLogMonitorThread.start()

        self.shimLogMonitorThread = LogMonitorThread(self.shimTool.shimLog)
        self.shimLogMonitorThread.update_log.connect(partial(self.updateLogOutput, self.shimLogOutput))
        self.shimLogMonitorThread.start()
    
    def setupBasicTabLayout(self, layout: QBoxLayout):
        """ Setup the layout for the basic tab.  This tab contains the main controls for the Exsi system, such as calibration, scanning, and ROI selection.
        """

        # basic layout is horizontal
        basicLayout = QHBoxLayout()
        layout.addLayout(basicLayout)

        # Add imageLayout to the basicLayout
        imageLayout = QVBoxLayout()
        basicLayout.addLayout(imageLayout)

        # Slider for selecting slices
        packed = addLabeledSliderAndEntry(imageLayout, "Slice Index (Int): ", self.updateROIImageDisplay)
        self.roiSliceIndexSlider, self.roiSliceIndexEntry = packed
        self.roiSliceIndexSlider.setEnabled(False) 
        self.roiSliceIndexEntry.setEnabled(False) # start off disabled -- no image is viewed yet!

        # setup image scene and view
        self.roiViewLabel = QLabel()
        self.roiView = ImageViewer(self, self.roiViewLabel)
        self.roiView.setFixedSize(512, 512)  # Set a fixed size for the view
        self.roiView.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        imageLayout.addWidget(self.roiView, alignment=Qt.AlignmentFlag.AlignCenter)
        imageLayout.addWidget(self.roiViewLabel)
        self.views += [self.roiView]

        # sliders for the ROI editor, as in xyz size and position sliders
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
            self.roiSizeSliders[i] = addLabeledSlider(roiSizeSliders, f"Size {label[i]}", self.roiSliderGranularity)
            self.roiSizeSliders[i].valueChanged.connect(self.visualizeROI)
            self.roiSizeSliders[i].setEnabled(False)

            self.roiPositionSliders[i] = addLabeledSlider(roiPositionSliders, f"Center {label[i]}", self.roiSliderGranularity)
            self.roiPositionSliders[i].valueChanged.connect(self.visualizeROI)
            self.roiPositionSliders[i].setEnabled(False)

        self.roiToggleButton = addButtonConnectedToFunction(imageLayout, "Enable ROI Editor", self.toggleROIEditor)

        # Controls and log layout
        controlsLayout = QVBoxLayout()
        basicLayout.addLayout(controlsLayout)
        self.setupExsiButtonsAndLog(controlsLayout) # another UI Function

    def setupExsiButtonsAndLog(self, layout: QBoxLayout):
        # create the buttons
        self.reconnectExsiButton = addButtonConnectedToFunction(layout, "Reconnect EXSI", self.shimTool.exsiInstance.connectExsi)
        self.doCalibrationScanButton = addButtonConnectedToFunction(layout, "Do Calibration Scan", self.doCalibrationScan)
        self.doFgreScanButton = addButtonConnectedToFunction(layout, "Do FGRE Scan", self.doFgreScan)
        self.renderLatestDataButton = addButtonConnectedToFunction(layout, "Render Data", self.doGetAndSetROIImage)
        self.slowButtons += [self.doCalibrationScanButton, self.doFgreScanButton]

        # radio button group for selecting which roi view you want to see
        roiVizButtonWindow = QWidget()
        roiVizButtonLayout = QHBoxLayout()
        roiVizButtonWindow.setLayout(roiVizButtonLayout)
        self.roiVizButtonGroup = QButtonGroup(roiVizButtonWindow)
        layout.addWidget(roiVizButtonWindow)

        # Create the radio buttons
        roiLatestDataButton = QRadioButton("Latest Data")
        roiBackgroundButton = QRadioButton("Background Mag.")
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

    def setupShimmingTabLayout(self, layout: QBoxLayout):
        """ Setup UI Layout for shimming tab"""
        
        shimSplitLayout = QHBoxLayout()
        layout.addLayout(shimSplitLayout)

        # make the left and right vboxex
        leftLayout = QVBoxLayout()
        rightLayout = QVBoxLayout()
        shimSplitLayout.addLayout(leftLayout)
        shimSplitLayout.addLayout(rightLayout)

        self.setupShimLeftView(leftLayout)
        self.setupShimRightView(rightLayout)

    def setupShimLeftView(self, layout: QBoxLayout):
        """ Setup UI for shim left view
            Include b0map viewer, and statistics printout
        """
        # add radio button group for selecting which shim view you want to see....
        # Create the QButtonGroup
        shimVizButtonWindow = QWidget()
        shimVizButtonLayout = QHBoxLayout()
        shimVizButtonWindow.setLayout(shimVizButtonLayout)
        self.shimVizButtonGroup = QButtonGroup(shimVizButtonWindow)
        layout.addWidget(shimVizButtonWindow)

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
        # TODO issue #7 add a function to do all this and also make the color bar inherent
        self.shimViewLabel = QLabel()
        self.shimView = ImageViewer(self, self.shimViewLabel)
        layout.addWidget(self.shimView, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.shimViewLabel)
        self.shimView.setFixedSize(512, 512)  # Set a fixed size for the view
        self.shimView.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.views += [self.shimView]

        # add three statistics outputs using another non editable textedit widget
        shimStatTextLabel = QLabel("Image Statistics")
        self.shimStatText = []
        statsLayout = QHBoxLayout()
        for _ in range(3):
            statTxt = QTextEdit()
            statTxt.setReadOnly(True)
            self.shimStatText.append(statTxt)
            statsLayout.addWidget(statTxt)
        layout.addWidget(shimStatTextLabel)
        layout.addLayout(statsLayout)

        self.saveResultsButton = addButtonConnectedToFunction(layout, "Save results", self.shimTool.saveResults)

    def setupShimRightView(self, layout: QBoxLayout):
        """Setup the right side of the shim tab view"""

        self.setupManualShimLayout(layout)

        # shim scan configs
        self.setupShimScanConfigurations(layout)

        # ACTUAL SHIM OPERATIONS START
        self.setupShimScanUI(layout)

        # Add the log output here
        self.shimLogOutput = QTextEdit()
        self.shimLogOutput.setReadOnly(True)
        self.shimLogOutputLabel = QLabel("SHIM Log Output")
        layout.addWidget(self.shimLogOutputLabel)
        layout.addWidget(self.shimLogOutput)
    
    def setupManualShimLayout(self, layout: QBoxLayout):
        """Create the ui for buttons and entries to allow user to manually set shim currents via the shim instance."""

        manualShimLayoutV = QVBoxLayout()
        containerWidget = QWidget()
        containerWidget.setObjectName("withBorder")
        containerWidget.setLayout(manualShimLayoutV)
        layout.addWidget(containerWidget)

        self.doManualShimLabel = QLabel(f"MANUAL SHIM OPERATIONS")
        manualShimLayoutV.addWidget(self.doManualShimLabel)

        manualShimLayoutH = QHBoxLayout()
        manualShimLayoutV.addLayout(manualShimLayoutH)

        calZeroGetcurrentLayout = QVBoxLayout()
        manualShimLayoutH.addLayout(calZeroGetcurrentLayout)

        # add the calibrate zero and get current buttons to left of manualShimLayout
        # all the handler functions execute in separate threads!
        manualShimButtonsLayout = QVBoxLayout()
        manualShimLayoutH.addLayout(manualShimButtonsLayout)
        self.shimCalChannelsButton = addButtonConnectedToFunction(manualShimButtonsLayout, "Calibrate Shim Channels", 
                                                                  self.shimTool.shimInstance.shimCalibrate)
        self.shimZeroButton        = addButtonConnectedToFunction(manualShimButtonsLayout, "Zero Shim Channels", 
                                                                  self.shimTool.shimInstance.shimZero)
        self.shimGetCurrentsButton = addButtonConnectedToFunction(manualShimButtonsLayout, "Get Shim Currents", 
                                                                  self.shimTool.shimInstance.shimGetCurrent)
        # add the vertical region for channel input, current input, and set current button right of manualShimLayout
        setChannelCurrentShimLayout = QVBoxLayout()
        manualShimLayoutH.addLayout(setChannelCurrentShimLayout)
        self.shimManualChannelEntry = addEntryWithLabel(setChannelCurrentShimLayout, "Channel Index (Int): ", 
                                                        QIntValidator(0, self.shimTool.shimInstance.numLoops-1))
        self.shimManualCurrenEntry = addEntryWithLabel(setChannelCurrentShimLayout, "Current (A): ", 
                                                       QDoubleValidator(-2.4, 2.4, 2))
        self.shimManualSetCurrentButton = addButtonConnectedToFunction(setChannelCurrentShimLayout, "Shim: Set Currents", 
                                                                       self.shimSetManualCurrent)
        self.slowButtons += [self.shimCalChannelsButton, self.shimZeroButton, self.shimGetCurrentsButton, self.shimManualSetCurrentButton]

    def setupShimScanConfigurations(self, layout: QBoxLayout):
        """Create UI buttons for configuring scan parameters for Shimming: such as delta TE and calibration strengths."""

        # update functions since these slider states need to be connected to the shimTool, and also follow the addLabeledSliderAndEntry pattern
        def updateDeltaTE(value): 
            self.shimTool.deltaTE = value
        def updateLoopCalCurrent(value): 
            self.shimTool.loopCalCurrent = value
        def updateGradientCalStrength(value): 
            self.shimTool.gradientCalStrength = value

        boundingWidget = QWidget()
        boundingWidget.setObjectName("withBorder")
        layout.addWidget(boundingWidget)
        boundingLayout = QVBoxLayout()
        boundingWidget.setLayout(boundingLayout)

        # add label
        self.shimScanConfigLabel = QLabel("SHIM SCAN CONFIGS")
        boundingLayout.addWidget(self.shimScanConfigLabel)

        # delta TE slider and entry
        packed = addLabeledSliderAndEntry(boundingLayout, "Delta TE (us): ", updateDeltaTE)
        self.shimDeltaTESlider, self.shimDeltaTEEntry = packed
        updateSliderEntryLimits(*packed, self.shimTool.minDeltaTE, self.shimTool.maxDeltaTE, 
                                self.shimTool.deltaTE)

        # calibration strengths 
        calibrationStrengthView = QHBoxLayout()
        boundingLayout.addLayout(calibrationStrengthView)

        # for the gradient strengths
        packed = addLabeledSliderAndEntry(calibrationStrengthView, "Gradient Cal (tick?): ", updateGradientCalStrength)
        self.shimGradientStrengthSlider, self.shimGradientStrengthEntry = packed
        updateSliderEntryLimits(*packed, self.shimTool.minGradientCalStrength, self.shimTool.maxGradientCalStrength, 
                                self.shimTool.gradientCalStrength)
        
        # for current slider and entry
        packed = addLabeledSliderAndEntry(calibrationStrengthView, "Loop Cal (mA): ", updateLoopCalCurrent)
        self.shimCalCurrentSlider, self.shimCalCurrentEntry = packed
        updateSliderEntryLimits(*packed, self.shimTool.minCalibrationCurrent, self.shimTool.maxCalibrationCurrent, 
                                self.shimTool.loopCalCurrent)
        self.slowButtons += [self.shimDeltaTESlider, self.shimDeltaTEEntry, 
                             self.shimGradientStrengthSlider, self.shimGradientStrengthEntry,
                             self.shimCalCurrentSlider, self.shimCalCurrentEntry]

    def setupShimScanUI(self, layout):
        """
        Create UI for actually driving the Shimming process
        """
        boundingWidget = QWidget()
        boundingWidget.setObjectName("withBorder")
        layout.addWidget(boundingWidget)
        boundingLayout = QVBoxLayout()
        boundingWidget.setLayout(boundingLayout)

        self.doShimProcedureLabel = QLabel("SHIM OPERATIONS | CF:_, CenterGradients:_")
        boundingLayout.addWidget(self.doShimProcedureLabel)

        # add the config selections for FieldMap

        configWidget = QWidget()
        configWidget.setObjectName("withBorderDotted")
        boundingLayout.addWidget(configWidget)
        configLayout = QVBoxLayout()
        configWidget.setLayout(configLayout)

        selectHLayout = QHBoxLayout()
        configLayout.addLayout(selectHLayout)

        scanSettingsLayout = QVBoxLayout()
        selectHLayout.addLayout(scanSettingsLayout)

        # add checkboxes for autoprescan and for overwriting the current background 
        self.doAutoPrescanMarker = QCheckBox("Auto Prescan?")
        self.doAutoPrescanMarker.setChecked(True)
        self.doAutoPrescanMarker.setEnabled(False)
        scanSettingsLayout.addWidget(self.doAutoPrescanMarker)
        self.doAutoPrescanMarker.stateChanged.connect(self.toggleAutoPrescan)

        # add the radio buttons to select if it is a volume or slice-wise shim operation
        self.volumeSliceShimWidget = QWidget()
        scanSettingsLayout.addWidget(self.volumeSliceShimWidget)
        shimTypeLayout = QVBoxLayout()
        self.volumeSliceShimWidget.setLayout(shimTypeLayout)
        self.volumeSliceShimButtonGroup = QButtonGroup(self.volumeSliceShimWidget)
        sliceShimRadioButton = QRadioButton("Perform Slice Shim")
        volumeShimRadioButton = QRadioButton("Perform Volume Shim")
        sliceShimRadioButton.setChecked(True)
        self.volumeSliceShimButtonGroup.addButton(sliceShimRadioButton, 0)
        self.volumeSliceShimButtonGroup.addButton(volumeShimRadioButton, 1)
        shimTypeLayout.addWidget(sliceShimRadioButton)
        shimTypeLayout.addWidget(volumeShimRadioButton)
        self.volumeSliceShimButtonGroup.idClicked.connect(self.toggleShimStyleRadio)

        # add buttons that control settings for the scans
        settingsButtons = QVBoxLayout()
        selectHLayout.addLayout(settingsButtons)

        self.doOverwriteBackgroundButton = addButtonConnectedToFunction(settingsButtons, "Overwrite Background?", self.overwriteBackground)
        self.doOverwriteBackgroundButton.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed))
        self.doOverwriteBackgroundButton.setEnabled(False) # this should become checkable when we have the first "actual shimmed background" completed

        # add the button to recompute shim solutions
        self.recomputeCurrentsButton = addButtonConnectedToFunction(settingsButtons, "Recompute\nShim Solutions", self.recomputeCurrentsAndView)
        self.recomputeCurrentsButton.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed))
        # set this to false to begin with because the currents are not computed yet
        self.recomputeCurrentsButton.setEnabled(False) # enables when the currents are computed i.e. basis map button is done

        # add the slice selection slider
        packed = addLabeledSliderAndEntry(configLayout, "Slice Index (Int): ", self.updateShimImageAndStats)
        self.shimSliceIndexSlider, self.shimSliceIndexEntry = packed
        self.shimSliceIndexSlider.setEnabled(False)
        self.shimSliceIndexEntry.setEnabled(False)

        # add the Solved Currents Display and the Set Shim Button
        solutionAndSetCurrentLayout = QHBoxLayout()
        configLayout.addLayout(solutionAndSetCurrentLayout)

        self.currentsDisplay = QTextEdit()
        line_height = QFontMetrics(self.currentsDisplay.font()).lineSpacing()
        height = line_height * 2.5
        self.currentsDisplay.setFixedHeight(int(height))
        self.currentsDisplay.setReadOnly(True)
        solutionAndSetCurrentLayout.addWidget(self.currentsDisplay)

        self.setShimButton = addButtonConnectedToFunction(solutionAndSetCurrentLayout, "Apply Shim\nSolutions", self.setAllShimCurrents)
        self.setShimButton.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed))
        # set this to false to begin with because the currents are not computed yet
        self.setShimButton.setEnabled(False) # enables when the currents are computed i.e. basis map button is done


        # macros for obtaining background scans
        self.doBackgroundScansButton = addButtonConnectedToFunction(configLayout, "Obtain Field Map", self.doFieldmapScan)

        self.doLoopCalibrationScansButton = addButtonConnectedToFunction(boundingLayout, "Obtain Basis Field Maps", self.doLoopCalibrationScans)
        self.slowButtons += [self.recomputeCurrentsButton,
                             self.setShimButton,
                             self.doBackgroundScansButton, 
                             self.doLoopCalibrationScansButton]

        if self.debugging:
            debuglabel = QLabel("Debug Only Below")
            debuglabel.setObjectName("withBorderDottedTop")
            boundingLayout.addWidget(debuglabel)
            self.doEvalApplShimsButton = addButtonConnectedToFunction(boundingLayout, "Evaluate Applied Shims", self.doEvalAppliedShims)
            self.doAllShimmedScansButton = addButtonConnectedToFunction(boundingLayout, "Shimmed Scan ALL Slices", self.doAllShimmedScans)

            self.slowButtons += [self.doEvalApplShimsButton, self.doAllShimmedScansButton]

    def setup3rdTabLayout(self, layout: QBoxLayout):
        """
        add two panes, left for visualizing each basis function, via a slider, right for visualizing the histograms of shimmed results and such
        """
        hlayout = QHBoxLayout()
        layout.addLayout(hlayout)

        # LEFT
        leftLayout = QVBoxLayout()
        hlayout.addLayout(leftLayout)

        # add a graphics view for visualizing the basis function
        self.basicViewLabel = QLabel()
        self.basisView = ImageViewer(self, self.basicViewLabel)
        leftLayout.addWidget(self.basisView, alignment=Qt.AlignmentFlag.AlignCenter)
        leftLayout.addWidget(self.basicViewLabel)
        self.basisView.setFixedSize(512, 512)  # Set a fixed size for the view
        self.basisView.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.views += [self.basisView]

        # add a slider for selecting the basis function
        numbasis = self.shimTool.shimInstance.numLoops + 3
        self.basisFunctionSlider, self.basisFunctionEntry = addLabeledSliderAndEntry(leftLayout, "Basis Function Index (Int): ", self.updateBasisView)
        updateSliderEntryLimits(self.basisFunctionSlider, self.basisFunctionEntry, 0, numbasis - 1, 0)

        # add a label and select for the slice index
        self.basisSliceIndexSlider, self.basisSliceIndexEntry = addLabeledSliderAndEntry(leftLayout, "Slice Index (Int): ", self.updateBasisView)
        self.basisSliceIndexEntry.setEnabled(False)
        self.basisSliceIndexEntry.setEnabled(False)

    # ---------- Slider Get Functions ----------- #
    def getROISliceIndex(self):
        return self.roiSliceIndexSlider.value()
    def getROIBackgroundSelected(self):
        """get the selected roi view type. 0 for latest data, 1 for background"""
        return self.roiVizButtonGroup.checkedId()
    def getShimViewTypeSelected(self):
        """Get the selected shim view type. 0 for background, 1 for estimated background, 2 for actual shimmed background."""
        return self.shimVizButtonGroup.checkedId()
    def getShimSliceIndex(self):
        return self.shimSliceIndexSlider.value()
    def getShimDeltaTE(self):
        return self.shimDeltaTESlider.value()
    def getShimCalCurrent(self):
        return self.shimCalCurrentSlider.value() / 1000 # convert to A
    def getBasisSliceIndex(self):
        return self.basisSliceIndexSlider.value()
    def getBasisFunctionIndex(self):
        return self.basisFunctionSlider.value()


    # ---------- State Functions ---------- #

    def saveState(self):
        """Save the current state of the GUI."""
        self.shimTool.saveState()

        for attr in self.state.keys():
            if attr != 'checkboxes':
                self.state[attr] = getattr(self, attr)

        # get the latest progress that we have made
        for checkboxName in self.state['checkboxes'].keys():
            checked = getattr(self, checkboxName).isChecked()
            self.log(f"saving {checkboxName} to {checked}")
            self.state['checkboxes'][checkboxName] = checked
        
        with open(self.latestStateSavePath, 'wb') as file:
            # Pickle the entire dictionary of attributes this object has
            pickle.dump(self.state, file)

    def loadState(self):
        """Load the latest saved state of the GUI. Then run all the update functions with respect to the new data"""
    
        self.shimTool.loadState()
        
        # return if a last save file doesn't exist
        if not os.path.exists(self.latestStateSavePath):
            return
        with open(self.latestStateSavePath, 'rb') as file:
            # Load the attributes and set them back to the class
            self.state = pickle.load(file)
            for name, value in self.state.items():
                setattr(self, name, value)        
            for checkboxName, value in self.state['checkboxes'].items():
                self.log(f"Seting {checkboxName} to {value}")
                getattr(self, checkboxName).setChecked(value)
            
        # re mask / update visualization
        self.shimTool.applyMask()
        self.updateAllDisplays()

    # ---------- Update Functions ---------- #
    def renameTab(self, tab, tabName):
        """
        Rename the given tab with the given name
        should be called by the shim tool to rename once connections have been made
        """
        index = self.centralTabWidget.indexOf(tab)
        self.centralTabWidget.setTabText(index, tabName)
    
    def setWindowAndExamNumber(self, examNumber=None, patientName=None):
        """Set the window title of the application."""
        self.guiWindowTitle = f"[ Shim Control GUI | EXAM: {examNumber or '!'} |" + \
                              f" Patient: {patientName or '!'} ]"
        self.setWindowTitle(self.guiWindowTitle)
    
    def onTabSwitch(self, index):
        self.log(f"Switched to tab {index}")
        if index == 0:
            if self.shimTool.obtainedBackground():
                self.roiVizButtonGroup.buttons()[1].setEnabled(True)
        if index == 1:
            self.log(f"did we get updated ROI? {self.shimTool.ROI.updated}")
            if self.shimTool.ROI.updated:
                self.recomputeCurrentsAndView()
            self.shimTool.ROI.updated = False
        if index == 2:
            self.updateBasisView()
    
    def toggleAutoPrescan(self, state):
        self.shimTool.autoPrescanDone = not (state == 2) # set it to not done so it gets set next time

    def overwriteBackground(self):
        self.shimTool.overwriteBackground(self.getShimSliceIndex())
        self.updateShimImageAndStats()
    
    def toggleShimStyleRadio(self, id):
        self.shimTool.shimMode = id
        if self.shimTool.obtainedBackground():
            self.shimSliceIndexSlider.setEnabled(id == 0)
            self.shimSliceIndexEntry.setEnabled(id == 0)

    def updateLogOutput(self, log, text):
        log.append(text)

    def setView(self, qImage: QImage, view: ImageViewer):
        """Sets the view of the ImageViewer to the given QImage."""
        pixmap = QPixmap.fromImage(qImage)
        view.viewport().setVisible(True)
        view.set_pixmap(pixmap)
        view.setSceneRect(view.pixmap_item.boundingRect())  # Adjust scene size to the pixmap's bounding rect
        view.fitInView(view.pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)  # Fit the view to the item
        view.viewport().update()  # Force the viewport to update

    def updateAllDisplays(self):
        # actions to update the rest of the GUI objects with the loaded data
        self.updateROIImageDisplay()
        self.updateShimImageAndStats()
        self.updateBasisView()
        if self.shimTool.obtainedBackground():
            self.roiVizButtonGroup.buttons()[1].setEnabled(True)

    def updateDisplay(self, viewIndex):
        """Update a specific view with the corresponding underlying data available."""
        viewDataSlice = self.viewDataSlice[viewIndex] # this should be a 2d numpy array now
        # if view data is not none, then so should the slice and maxAbs value
        if viewDataSlice is not None:
            # Extract the slice and normalize it
            scale = self.viewMaxAbs[viewIndex]
            if viewIndex > 0:
                # when we are looking at b0maps, the numbers can be negative
                scale = 2*scale 
                normalizedData = (viewDataSlice - np.nanmin(viewDataSlice)) / scale * 127 + 127
            else:
                normalizedData = (viewDataSlice - np.nanmin(viewDataSlice)) / scale * 255
            # make the value 127 (correlates to 0 Hz offset) wherever it is outside of mask
            normalizedData[np.isnan(viewDataSlice)] = 0
            # specific pyqt6 stuff to convert numpy array to QImage
            displayData = np.ascontiguousarray(normalizedData).astype(np.uint8)
            # stack 4 times for R, G, B, and alpha value
            rgbData = np.stack((displayData,)*3, axis=-1)
            height, width, _ = rgbData.shape
            bytesPerLine = rgbData.strides[0] 
            qImage = QImage(rgbData.data, width, height, bytesPerLine, QImage.Format.Format_RGB888)
            # set the actual view that we care about
            self.views[viewIndex].qImage = qImage
            self.views[viewIndex].viewData = viewDataSlice
            self.setView(qImage, self.views[viewIndex])

        
    def visualizeROI(self):
        """
        Visualize the ROI based on the current selected shape type slider values.
        """
        if not self.shimTool.ROI or not self.views[0].qImage:
            raise GuiError("ROI object or qImage not already initialized and trying to visualize ROI")
        if not self.shimTool.ROI.enabled or self.roiVizButtonGroup.checkedId() == 0 or not self.shimTool.obtainedBackground():
            return # dont do anything because either the toggle is not enabled,

        # TODO: add support for other ROI shapes and selection here
        self.shimTool.ROI = self.shimTool.ROI

        old = self.shimTool.ROI.sizes + self.shimTool.ROI.centers

        # Update the ROI values based on the sliders
        for i in range(3):
            self.shimTool.ROI.sliderSizes[i] = self.roiSizeSliders[i].value() / self.roiSliderGranularity
            self.shimTool.ROI.sliderCenters[i] = self.roiPositionSliders[i].value() / self.roiSliderGranularity
        
        qImage = self.views[0].qImage
        depth = self.shimTool.viewData[0].shape[0]

        # scale relative to the image size
        self.shimTool.ROI.sizes[0] = max(1, round((self.shimTool.ROI.xdim // 2) * self.shimTool.ROI.sliderSizes[0]))
        self.shimTool.ROI.sizes[1] = max(1, round((self.shimTool.ROI.ydim // 2) * self.shimTool.ROI.sliderSizes[1]))
        self.shimTool.ROI.sizes[2] = max(1, round((self.shimTool.ROI.zdim // 2) * self.shimTool.ROI.sliderSizes[2]))
        self.shimTool.ROI.centers[0] = round(self.shimTool.ROI.xdim * self.shimTool.ROI.sliderCenters[0])
        self.shimTool.ROI.centers[1] = round(self.shimTool.ROI.ydim * self.shimTool.ROI.sliderCenters[1])
        self.shimTool.ROI.centers[2] = round(self.shimTool.ROI.zdim * self.shimTool.ROI.sliderCenters[2])

        # check if the ROI has been updated
        if old != self.shimTool.ROI.sizes + self.shimTool.ROI.centers:
            self.shimTool.ROI.updated = True

        sliceIdx = self.roiSliceIndexSlider.value()
        offsetFromDepthCenter = abs(sliceIdx - self.shimTool.ROI.centers[2])
        drawingQImage = qImage.copy()
        if offsetFromDepthCenter <= self.shimTool.ROI.sizes[2]:
            painter = QPainter(drawingQImage)
            # Set the pen color to red and the brush to a semi transparent red
            painter.setPen(QPen(QBrush(QColor(255, 0, 0, 100)), 1))
            points = self.shimTool.ROI.getSlicePoints(sliceIdx)
            for x, y in points:
                painter.drawPoint(x, y)
            painter.end()
        
        self.setView(drawingQImage, self.views[0])
    
    def validateROIInput(self):
        """Validate the ROI input sliders and entries."""
        # check that there is some data in viewData before trying this
        if self.shimTool.viewData[0] is None:
            self.log("No data available to visualize ROI.")
            return False

        # there is reason to enable the slice index slider now, if it wasn't already
        self.roiSliceIndexSlider.setEnabled(True)
        self.roiSliceIndexEntry.setEnabled(True)
        # the data should have already been placed into viewData

        # if background acquired, toggle the background button
        if self.shimTool.obtainedBackground():
            self.roiVizButtonGroup.buttons()[1].setEnabled(True)

        # get the max abs value of the background data
        self.viewMaxAbs[0] = np.max(np.abs(self.shimTool.viewData[0]))
        # if the data is background
        if self.roiVizButtonGroup.checkedId() == 1:
            if not self.shimTool.obtainedBackground():
                raise GuiError("Background scans not yet obtained.")
            # need to set the slider limits
            upperlimit = self.shimTool.viewData[0].shape[1]-1
            updateSliderEntryLimits(self.roiSliceIndexSlider, self.roiSliceIndexEntry, 
                                    0, upperlimit)
                                    # assume that slider value automatically updated to be within bounds
            # need to set viewDataSlice to the desired slice
            self.viewDataSlice[0] = self.shimTool.viewData[0][:,self.roiSliceIndexSlider.value(),:]
            # set ROI limits TODO issue #1
            ydim, zdim, xdim = self.shimTool.viewData[0].shape
            self.shimTool.ROI.setROILimits(xdim, ydim, zdim)
        # if the data is latest data
        else:
            upperlimit = self.shimTool.viewData[0].shape[0]-1
            updateSliderEntryLimits(self.roiSliceIndexSlider, self.roiSliceIndexEntry, 
                                    0, upperlimit)
                                    # assume that slider value automatically updated to be within bounds
            # need to set viewDataSlice to the desired slice
            self.viewDataSlice[0] = self.shimTool.viewData[0][self.roiSliceIndexSlider.value()]
        return True

    def updateROIImageDisplay(self):
        """Update the ROI image display based on the current slice index."""
        # validate the ranges and set the sliders enabled
        if self.validateROIInput():
            self.updateDisplay(0)
            self.visualizeROI()

    def toggleROIBackgroundImage(self):
        if self.roiVizButtonGroup.checkedId() == 1 and self.shimTool.obtainedBackground():
            self.roiToggleButton.setEnabled(True)

    def toggleROIEditor(self):
        self.shimTool.ROI.updated = True
        if self.roiVizButtonGroup.checkedId() == 1:
            if self.shimTool.ROI.enabled:
                self.shimTool.ROI.enabled = False
                self.roiToggleButton.setText("Enable ROI Editor")
                self.shimTool.roiMask = None
            else:
                self.shimTool.ROI.enabled = True
                self.roiToggleButton.setText("Disable ROI Editor")
                for i in range(3):
                    self.roiSizeSliders[i].setEnabled(True)
                    self.roiPositionSliders[i].setEnabled(True)
            self.updateROIImageDisplay()

    def validateShimInputs(self):
        """Validate the shim input sliders and entries.
           The data should already be updated to be masked with final ROI"""

        # get the selected shimView type from the radio button 
        selectedShimView = self.getShimViewTypeSelected()

        # check that there even is data to visualize to begin with
        if self.shimTool.viewData[1][selectedShimView] is None:
            return False

        # get the max abs value of the shimView data set        
        for i in range(self.shimTool.viewData[1].shape[0]):
            if self.shimTool.viewData[1][i] is not None:
                self.viewMaxAbs[1] = max(self.viewMaxAbs[1], np.nanmax(np.abs(self.shimTool.viewData[1][i])))

        # set the limit to the shim slice index slider 
        upperlimit = self.shimTool.viewData[1][0].shape[1]-1
        updateSliderEntryLimits(self.shimSliceIndexSlider, self.shimSliceIndexEntry,
                                0, upperlimit)
                                # assume that slider value automatically updated to be within bounds
        # there is reason to enable the slice index slider now, if it wasn't already
        self.shimSliceIndexSlider.setEnabled(True)
        self.shimSliceIndexEntry.setEnabled(True)
        # set the viewDataSlice to the desired slice from the whole 4D data set
        self.viewDataSlice[1] = self.shimTool.viewData[1][selectedShimView][:,self.shimSliceIndexSlider.value(),:]
        return True
    
    def updateShimStats(self):
        """Update the shim statistics text boxes"""
        # update the checkbox configurations after the latest scan
        if self.shimTool.obtainedBackground():
            self.doAutoPrescanMarker.setEnabled(True)
            self.doOverwriteBackgroundButton.setEnabled(True)
            self.recomputeCurrentsButton.setEnabled(True)
            self.setShimButton.setEnabled(True)

        self.doAutoPrescanMarker.setChecked(not self.shimTool.autoPrescanDone)

        # update the rest of the stats
                
        # get the slice index from the slider
        sliceIndex = self.getShimSliceIndex()
        # set the text to the text boxes
        # show as many stats as are available for the specific slice
        prefixs = ["Background ", "Est. ", "Actual "]
        for i in range(3):
            text = "\nNo stats available"                
            if self.shimTool.shimStatStrs[i] is not None:
                stats = self.shimTool.shimStatStrs[i][sliceIndex]
                if stats is not None:
                    text = stats
            text = prefixs[i] + text
            self.shimStatText[i].setText(text)

        # if original gradients / original center frequency available 
        shimtxt = "Principle Sols: "
        shimtxt += f"OG CF = {self.shimTool.principleSols[0].astype(int)} Hz | "
        shimtxt += f"Default lin gradient shims = {self.shimTool.principleSols[1:4].astype(int)}"
        self.doShimProcedureLabel.setText(f"SHIM OPERATIONS; " + shimtxt)

        # if currents are available
        if self.shimTool.solutionValuesToApply is not None:
            if self.shimTool.solutionValuesToApply[sliceIndex] is not None:
                solutions = self.shimTool.solutionValuesToApply[sliceIndex]
                text = f"Δcf:{int(round(solutions[0]))} | "
                numIter = self.shimTool.shimInstance.numLoops + 3
                pref = ["x", "y", "z"]
                for i in range(3):
                    text += 'g' + pref[i]
                    text += f":{int(round(solutions[i+1]))} | "
                text += "\n"
                for i in range(3, numIter):
                    text += f"ch{i-3}:{solutions[i+1]:.2f} | "
            else:
                text = "No currents available "
            self.currentsDisplay.setText(text[:-1])
        else:
            self.currentsDisplay.setText("No currents available")

    def toggleShimImage(self, id):
        """re-render the shim image based on the selected radio button"""
        self.log(f"Toggling shim image to {id}")
        self.updateShimImageAndStats()
    
    def updateShimImageAndStats(self): 
        """Update the shim image display based on the current slice index, and the selected shim view type, along with stats"""
        if self.validateShimInputs():
            self.updateDisplay(1)
            self.updateShimStats()
        else:
            # display empty
            self.setView(QImage(), self.views[1])

    def validateBasisInputs(self):
        """Validate the inputs for the Basis Views"""

        # get the max abs value of the basisView data set
        for i in range(self.shimTool.viewData[2].shape[0]):
            if self.shimTool.viewData[2][i] is not None:
                self.viewMaxAbs[2] = max(self.viewMaxAbs[2], np.nanmax(np.abs(self.shimTool.viewData[2][i])))
            else:
                self.log("ERROR: Calibration scans done marker checked, but basis data is not available yet")
                return False # cancel the viewing, since clearly some of the data is not there yet...

        # set the limit to the basis slice index slider
        numslices = self.shimTool.viewData[2][0].shape[1]-1
        updateSliderEntryLimits(self.basisSliceIndexSlider, self.basisSliceIndexEntry,
                                0, numslices)

        # there is reason to enable the slice index slider now, if it wasn't already
        self.basisSliceIndexSlider.setEnabled(True)
        self.basisSliceIndexEntry.setEnabled(True)

        # set the viewDataSlice to the desired slice from the whole 4D data set
        self.viewDataSlice[2] = self.shimTool.viewData[2][self.basisFunctionSlider.value()][:,self.basisSliceIndexSlider.value(),:]
        return True

    def updateBasisView(self):
        """Update the basis function image display based on the current slice index and basis function index."""
        if self.shimTool.obtainedBasisMaps() and self.validateBasisInputs():
            self.updateDisplay(2)
            # TODO update histograms on the right.

    def log(self, msg):
        header = "GUI: "
        log(header + msg, self.debugging)
    
    # ----------- Button Functions ----------- #

    def disableSlowButtonsTillDone(func):
        """Decorator to wrap around slow button functions to disable other buttons until the function is done."""
        def wrapper(self):
            for button in self.slowButtons:
                button.setEnabled(False)
            func(self)
        return wrapper

    def requireShimConnection(func):
        """Decorator to check if the EXSI client is connected before running a function."""
        def wrapper(self, *args, **kwargs):
            # Check the status of the event
            if not self.shimTool.shimInstance.connectedEvent.is_set() and not self.debugging:
                # Show a message to the user, reconnect shim client.
                createMessageBox("SHIM Client Not Connected",
                                "The SHIM client is still not connected to shim arduino.", 
                                "Closing Client.\nCheck that arduino is connected to the HV Computer via USB.\n" +
                                "Check that the arduino port is set correctly using serial_finder.sh script.")
                self.close()
                return
            return func(self, *args, **kwargs)
        return wrapper

    def requireExsiConnection(func):
        """Decorator to check if the EXSI client is connected before running a function."""
        def wrapper(self, *args, **kwargs):
            # Check the status of the event
            if not self.shimTool.exsiInstance.connected_ready_event.is_set() and not self.debugging:
                # Show a message to the user, reconnect exsi client.
                createMessageBox("EXSI Client Not Connected", 
                                "The EXSI client is still not connected to scanner.", 
                                "Closing Client.\nCheck that External Host on scanner computer set to 'newHV'.")
                self.close()
                return
            return func(self, *args, **kwargs)
        return wrapper

    def requireAssetCalibration(func):
        """Decorator to check if the ASSET calibration scan is done before running a function."""
        def wrapper(self, *args, **kwargs):
            #TODO(rob): probably better to figure out how to look at existing scan state. somehow check all performed scans on start?
            if not self.shimTool.assetCalibrationDone and not self.debugging:
                self.log("Debug: Need to do calibration scan before running scan with ASSET.")
                # Show a message to the user, reconnect exsi client.
                createMessageBox("Asset Calibration Scan Not Performed",
                                "Asset Calibration scan not detected to be completed.", 
                                "Please perform calibration scan before continuing with this scan")
                return
            return func(self, *args, **kwargs)
        return wrapper

    def enableSlowButtons(self):
        for button in self.slowButtons:
            button.setEnabled(True)

    def shimSetManualCurrent(self):
        """Set the shim current to the value in the entry."""
        channel = int(self.shimManualChannelEntry.text())
        current = float(self.shimManualCurrenEntry.text())
        self.shimTool.shimInstance.shimSetCurrentManual(channel, current)
    
    @disableSlowButtonsTillDone
    @requireExsiConnection
    def doCalibrationScan(self):
        # dont need to do the assetCalibration scan more than once
        trigger = Trigger()
        def action():
            self.updateROIImageDisplay()
            self.enableSlowButtons()
        trigger.finished.connect(action)
        kickoff_thread(self.shimTool.doCalibrationScan, args=(trigger,))

    @disableSlowButtonsTillDone
    @requireExsiConnection
    @requireAssetCalibration
    def doFgreScan(self):
        trigger = Trigger()
        def action():
            self.updateROIImageDisplay()
            self.enableSlowButtons()
        trigger.finished.connect(action)
        kickoff_thread(self.shimTool.doFgreScan, args=(trigger,))
    
    @disableSlowButtonsTillDone
    @requireExsiConnection
    def doGetAndSetROIImage(self):
        trigger = Trigger()
        def action():
            self.updateROIImageDisplay()
            self.enableSlowButtons()
        trigger.finished.connect(action)
        kickoff_thread(self.shimTool.getAndSetROIImage, args=(self.getROIBackgroundSelected() == 1, trigger,))

    @disableSlowButtonsTillDone
    def recomputeCurrentsAndView(self):
        trigger = Trigger()
        def action():
            self.updateShimImageAndStats()
            self.enableSlowButtons()
        trigger.finished.connect(action)
        kickoff_thread(self.shimTool.recomputeCurrentsAndView, args=(trigger,))

    @disableSlowButtonsTillDone
    @requireExsiConnection
    @requireShimConnection
    @requireAssetCalibration
    def doFieldmapScan(self):
        # Perform the background scans for the shim system.
        trigger = Trigger()
        def actionAndUpdate():
            self.toggleShimStyleRadio(self.volumeSliceShimButtonGroup.checkedId())
            self.updateShimImageAndStats()
            self.enableSlowButtons()
        trigger.finished.connect(actionAndUpdate)
        if self.shimTool.shimModes[self.shimTool.shimMode] == "Slice-Wise" and self.shimTool.obtainedBackground():
            sliceIdx = self.getShimSliceIndex()
        else:
            sliceIdx = None
        kickoff_thread(self.shimTool.doFieldmapScan, args=(trigger,sliceIdx))

    @disableSlowButtonsTillDone
    @requireExsiConnection
    @requireShimConnection
    @requireAssetCalibration
    def doLoopCalibrationScans(self):
        if not self.shimTool.obtainedBackground():
            self.log(f'Need to perform background fieldmap scan before running loop calibration scans')
            return
        trigger = Trigger()
        def actionAndUpdate():
            self.updateShimImageAndStats()
            self.enableSlowButtons()
        trigger.finished.connect(actionAndUpdate)
        kickoff_thread(self.shimTool.doBasisCalibrationScans, args=(trigger,))

    @disableSlowButtonsTillDone
    @requireShimConnection
    def setAllShimCurrents(self):
        if not self.shimTool.obtainedSolutions():
            self.log("Need to perform background and loop calibration scans before setting currents.")
            createMessageBox("Error: Background And Loop Cal Scans not Done",
                             "Need to perform background and loop calibration scans before setting currents.", 
                             "You could set them manually if you wish to.")
            return # do nothing more
        trigger = Trigger()
        def actionAndUpdate():
            self.enableSlowButtons()
        trigger.finished.connect(actionAndUpdate)
        kickoff_thread(self.shimTool.setAllShimCurrents, args=(self.getShimSliceIndex(), trigger,))

    # @disableSlowButtonsTillDone
    # @requireExsiConnection
    # @requireShimConnection
    # @requireAssetCalibration
    # def doShimmedScans(self):
    #     """ Perform another set of scans now that it is shimmed """
    #     if not self.setAllCurrentsMarker.isChecked():
    #             createMessageBox("Note: Shim Process Not Performed",
    #                              "If you want correct shims, click above buttons and redo.", "")
    #             return
    #     self.doShimmedScansMarker.setChecked(False)
    #     def actionAndUpdate():
    #         self.updateShimImageAndStats()
    #         self.doShimmedScansMarker.setChecked(trigger.success)
    #         self.enableSlowButtons()
    #     trigger = Trigger()
    #     trigger.finished.connect(actionAndUpdate)
    #     kickoff_thread(self.shimTool.doShimmedScans, args=(self.getShimSliceIndex(), trigger,))

    @disableSlowButtonsTillDone
    @requireExsiConnection
    @requireShimConnection
    @requireAssetCalibration
    def doEvalAppliedShims(self):
        """Scan with supposed set shims and evaluate how far from expected they are."""
        if not self.shimTool.obtainedSolutions():
            self.log("Need to perform background and loop calibration scans before running Eval Scan")
            createMessageBox("Error: Background And Loop Cal Scans not Done",
                             "Need to perform background and loop calibration scans before setting currents.", 
                             "You could set them manually if you wish to.")
            return # do nothing more
        trigger = Trigger()
        def action():
            self.updateShimImageAndStats()
            self.enableSlowButtons()
        trigger.finished.connect(action)
        kickoff_thread(self.shimTool.doEvalAppliedShims, args=(self.getShimSliceIndex(), trigger,))

    @disableSlowButtonsTillDone
    @requireExsiConnection
    @requireShimConnection
    @requireAssetCalibration
    def doAllShimmedScans(self):
        if not self.shimTool.obtainedSolutions():
            self.log("Need to perform background and loop calibration scans before running Eval Scan")
            createMessageBox("Error: Background And Loop Cal Scans not Done",
                             "Need to perform background and loop calibration scans before setting currents.", 
                             "You could set them manually if you wish to.")
            return # do nothing more
        trigger = Trigger()
        def action():
            self.updateShimImageAndStats()
            self.enableSlowButtons()
        trigger.finished.connect(action)
        kickoff_thread(self.shimTool.doAllShimmedScans, args=(trigger,))


class GuiError(Exception):
    """Custom exception for GUI errors."""
    pass
