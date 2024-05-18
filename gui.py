"""
This module contains the GUI for the application.
Includes both ui instantiation, as well as the logic for populating UI with data
"""

from guiUtils import *
from utils import *
import pickle
from PyQt6.QtWidgets import QApplication, QMainWindow, QBoxLayout, QVBoxLayout, QWidget, QTextEdit, QLabel, QSlider, QHBoxLayout, QLineEdit, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsTextItem, QTabWidget, QCheckBox, QSizePolicy, QButtonGroup, QRadioButton, QGraphicsItem
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QImage, QDoubleValidator, QIntValidator, QPainter, QPen, QBrush, QColor

from exsi_client import exsi
from shim_client import shim
from shimTool import shimTool

class Gui(QMainWindow):
    """
    The ExsiGui class represents the main GUI window for controlling the Exsi system.
    It provides functionality for connecting to the scanner client, the Shim client,
    and performing various operations related to calibration, scanning, and shimming.
    """

    def __init__(self, debugging, shimTool: shimTool, exsiInstance: exsi, shimInstance: shim, scannerLog, shimLog):
        super().__init__()

        # ----- Tool Variables ----- # 
        self.latestStateSavePath = os.path.join("toolStates", "guiLatestState.pkl")
        self.debugging = debugging # tick this only when you continue to be devving...
        self.shimTool = shimTool
        self.exsiInstance = exsiInstance
        self.shimInstance = shimInstance
        self.scannerLog = scannerLog
        self.shimLog = shimLog

        # ----- GUI Properties that dont change ----- #
        # array of buttons that need to be disabled during slow operations in other threads
        self.slowButtons = []

        # how fine scrolling you want the ROI sliders to be. 100 is more than enough typically...
        self.roiSliderGranularity = 100

        # array of views. so that we can generalize update function for all views
        # should be roi view, shim view, and then basis view
        self.views = []

        # ----- GUI Properties that act as state, in addition to all the gui features that hold state ----- #

        # the 3d data for each respective view; they should be cropped with respect to the Final Mask when they are set by the shimTool
        self.viewData = np.array([None,  # 3D data, unfilled, for roi view
                                    # three sets of 3D data, unfilled, for shim view (background, estimated, actual)                                   
                                  np.array([None, None, None], dtype=object),
                                    # 4D data, unfilled, for basis views
                                  np.array([None for _ in range(self.shimTool.shimInstance.numLoops + 3)], dtype=object)], dtype=object)
        self.viewDataSlice = np.array([np.nan for _ in range(3)], dtype=object) # three sets of 2D Slice Data that is actually visualized

        # the value range for each view
        self.viewMaxAbs = [0 for _ in range(3)]

        # the default ROI object
        self.ROI = ellipsoidROI()

        # start building the state vector for the GUI. These are all the essential data structures that are necessary to reload the app
        self.state = {
            "ROI": self.ROI,
            "checkboxes": {}
        }

        # ----- GUI Initialization ----- #
        self.initUI()
        

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
            self.saveStateButton = addButtonConnectedToFunction(stateButtonLayout, "DEBUG ONLY: Save Current State", self.shimTool.saveState)
            self.loadStateButton = addButtonConnectedToFunction(stateButtonLayout, "DEBUG ONLY: Load Latest State", self.shimTool.loadState)

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
        self.centralTabWidget.addTab(self.exsiTab, "EXSI Control [Not Connected]")
        self.centralTabWidget.addTab(self.shimmingTab, "SHIM Control [Not Connected]")
        self.centralTabWidget.addTab(self.basisTab, "Basis/Performance Visualization")

        # Connect the log monitor
        self.exsiLogMonitorThread = LogMonitorThread(self.scannerLog)
        self.exsiLogMonitorThread.update_log.connect(partial(self.updateLogOutput, self.exsiLogOutput))
        self.exsiLogMonitorThread.start()

        self.shimLogMonitorThread = LogMonitorThread(self.shimLog)
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
        packed = addLabeledSliderAndEntry(imageLayout, "Slice Index (Int): ", QIntValidator(0, 0), self.updateROIImageDisplay)
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
        self.reconnectExsiButton = addButtonConnectedToFunction(layout, "Reconnect EXSI", self.exsiInstance.connectExsi)
        self.doCalibrationScanButton = addButtonConnectedToFunction(layout, "Do Calibration Scan", self.shimTool.doCalibrationScan)
        self.doFgreScanButton = addButtonConnectedToFunction(layout, "Do FGRE Scan", self.shimTool.doFgreScan)
        self.renderLatestDataButton = addButtonConnectedToFunction(layout, "Render Data", self.shimTool.doGetAndSetROIImage)
        self.slowButtons += [self.doCalibrationScanButton, self.doFgreScanButton, self.renderLatestDataButton]

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
        """setup the right side of the shim tab view"""

        # MANUAL SHIMMING OPERATIONS START
        # horizontal box to split up the calibrate zero and get current buttons from the manual set current button and entries 
        self.doManualShimLabel = QLabel(f"MANUAL SHIM OPERATIONS")
        layout.addWidget(self.doManualShimLabel)
        manualShimLayout = QHBoxLayout()
        layout.addLayout(manualShimLayout)

        calZeroGetcurrentLayout = QVBoxLayout()
        manualShimLayout.addLayout(calZeroGetcurrentLayout)

        # add the calibrate zero and get current buttons to left of manualShimLayout
        # all the handler functions execute in separate threads!
        manualShimButtonsLayout = QVBoxLayout()
        manualShimLayout.addLayout(manualShimButtonsLayout)
        self.shimCalChannelsButton = addButtonConnectedToFunction(manualShimButtonsLayout, "Calibrate Shim Channels", 
                                                                  self.shimInstance.shimCalibrate)
        self.shimZeroButton        = addButtonConnectedToFunction(manualShimButtonsLayout, "Zero Shim Channels", 
                                                                  self.shimInstance.shimZero)
        self.shimGetCurrentsButton = addButtonConnectedToFunction(manualShimButtonsLayout, "Get Shim Currents", 
                                                                  self.shimInstance.shimGetCurrent)
        # add the vertical region for channel input, current input, and set current button right of manualShimLayout
        setChannelCurrentShimLayout = QVBoxLayout()
        manualShimLayout.addLayout(setChannelCurrentShimLayout)
        self.shimManualChannelEntry = addEntryWithLabel(setChannelCurrentShimLayout, "Channel Index (Int): ", 
                                                        QIntValidator(0, self.shimInstance.numLoops-1))
        self.shimManualCurrenEntry = addEntryWithLabel(setChannelCurrentShimLayout, "Current (A): ", 
                                                       QDoubleValidator(-2.4, 2.4, 2))
        self.shimManualSetCurrentButton = addButtonConnectedToFunction(setChannelCurrentShimLayout, "Shim: Set Currents", 
                                                                       self.shimTool.shimSetManualCurrent)
        self.slowButtons += [self.shimCalChannelsButton, self.shimZeroButton, self.shimGetCurrentsButton, self.shimManualSetCurrentButton]


        # ACTUAL SHIM OPERATIONS START
        packed = addLabeledSliderAndEntry(layout, "Slice Index (Int): ", QIntValidator(0, 0), self.updateShimImageAndStats)
        self.shimSliceIndexSlider, self.shimSliceIndexEntry = packed
        self.shimSliceIndexSlider.setEnabled(False)
        self.shimSliceIndexEntry.setEnabled(False)

        recomputeLayout = QHBoxLayout()
        self.recomputeCurrentsButton = addButtonConnectedToFunction(recomputeLayout, "Shim: Recompute Currents", self.shimTool.recomputeCurrentsAndView)
        self.slowButtons += [self.recomputeCurrentsButton]
        self.currentsDisplay = QLineEdit()
        self.currentsDisplay.setReadOnly(True)
        recomputeLayout.addWidget(self.currentsDisplay)
        layout.addLayout(recomputeLayout)

        self.doShimProcedureLabel = QLabel("SHIM OPERATIONS; ___")
        layout.addWidget(self.doShimProcedureLabel)

        # delta TE slider and entry
        packed = addLabeledSliderAndEntry(layout, "Delta TE (us): ", 
                                          QIntValidator(self.shimTool.minDeltaTE, self.shimTool.maxDeltaTE), 
                                          lambda: None)
        self.shimDeltaTESlider, self.shimDeltaTEEntry = packed
        updateSliderEntryLimits(*packed, self.shimTool.minDeltaTE, self.shimTool.maxDeltaTE, 
                                QIntValidator(self.shimTool.maxDeltaTE, self.shimTool.maxDeltaTE), 
                                self.shimTool.defaultDeltaTE)
        # calibration current slider and entry
        packed = addLabeledSliderAndEntry(layout, "Calibration Current (mA): ", 
                                          QIntValidator(self.shimTool.minCalibrationCurrent, self.shimTool.maxCalibrationCurrent),
                                          lambda: None)
        self.shimCalCurrentSlider, self.shimCalCurrentEntry = packed
        updateSliderEntryLimits(*packed, self.shimTool.minCalibrationCurrent, self.shimTool.maxCalibrationCurrent, 
                                QIntValidator(self.shimTool.minCalibrationCurrent, self.shimTool.maxCalibrationCurrent), 
                                self.shimTool.defaultCalibrationCurrent)
        self.slowButtons += [self.shimDeltaTESlider, self.shimDeltaTEEntry, self.shimCalCurrentSlider, self.shimCalCurrentEntry]


        # macros for obtaining background scans
        self.doBackgroundScansButton, self.doBackgroundScansMarker = addButtonWithFuncAndMarker(layout, "Shim: Obtain Background B0map", 
                                                                                                self.shimTool.doBackgroundScans)
        self.state['checkboxes']['doBackgroundScansMarker'] = self.doBackgroundScansMarker.isChecked()
        loopCalibrationLayout = QHBoxLayout()
        layout.addLayout(loopCalibrationLayout)
        self.withLinGradMarker = QCheckBox("With Lin. Gradients")
        self.withLinGradMarker.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.withLinGradMarker.setEnabled(False)
        self.withLinGradMarker.setChecked(True)
        loopCalibrationLayout.addWidget(self.withLinGradMarker)
        self.doLoopCalibrationScansButton, self.doLoopCalibrationScansMarker = addButtonWithFuncAndMarker(loopCalibrationLayout, 
                                                                                                          "Shim: Obtain Loop Basis B0maps", 
                                                                                                          self.shimTool.doLoopCalibrationScans)
        self.state['checkboxes']['withLinGradMarker'] = self.withLinGradMarker.isChecked()
        self.state['checkboxes']['doLoopCalibrationScansMarker'] = self.doLoopCalibrationScansMarker.isChecked()


        setAllCurrentsLayout = QHBoxLayout() # need a checkbox in front of the set all currents button to show that the currents have been computed
        layout.addLayout(setAllCurrentsLayout)
        self.currentsComputedMarker = QCheckBox("Currents Computed?")
        self.currentsComputedMarker.setEnabled(False)
        self.currentsComputedMarker.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        setAllCurrentsLayout.addWidget(self.currentsComputedMarker)
        self.setAllCurrentsButton, self.setAllCurrentsMarker = addButtonWithFuncAndMarker(setAllCurrentsLayout, "Current Selected Slice: Set Optimal Currents",
                                                                                          self.shimTool.shimSetAllCurrents)
        self.doShimmedScansButton, self.doShimmedScansMarker = addButtonWithFuncAndMarker(layout, "Current Selected Slice: Perform Shimmed Scan", 
                                                                                          self.shimTool.doShimmedScans)
        self.state['checkboxes']['currentsComputedMarker'] = self.currentsComputedMarker.isChecked()
        self.state['checkboxes']['setAllCurrentsMarker'] = self.setAllCurrentsMarker.isChecked()
        self.state['checkboxes']['doShimmedScansMarker'] = self.doShimmedScansMarker.isChecked()
        self.doEvalApplShimsButton = addButtonConnectedToFunction(layout, "Evaluate Applied Shims", self.shimTool.doEvalAppliedShims)

        self.doAllShimmedScansButton, self.doAllShimmedScansMarker = addButtonWithFuncAndMarker(layout, "Shimmed Scan ALL Slices", 
                                                                                                self.shimTool.doAllShimmedScans)
        self.slowButtons += [self.doBackgroundScansButton, 
                             self.doLoopCalibrationScansButton,
                             self.setAllCurrentsButton, 
                             self.doShimmedScansButton, 
                             self.doEvalApplShimsButton, 
                             self.doAllShimmedScansButton]

        # Add the log output here
        self.shimLogOutput = QTextEdit()
        self.shimLogOutput.setReadOnly(True)
        self.shimLogOutputLabel = QLabel("SHIM Log Output")
        layout.addWidget(self.shimLogOutputLabel)
        layout.addWidget(self.shimLogOutput)

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
        numbasis = self.shimInstance.numLoops + 3
        self.basisFunctionSlider, self.basisFunctionEntry = addLabeledSliderAndEntry(leftLayout, 
                                                                                     "Basis Function Index (Int): ", 
                                                                                     QIntValidator(0, numbasis - 1), 
                                                                                     self.updateBasisView)
        updateSliderEntryLimits(self.basisFunctionSlider, self.basisFunctionEntry, 0, numbasis - 1, QIntValidator(0, numbasis - 1), 0)

        # add a label and select for the slice index
        self.basisSliceIndexSlider, self.basisSliceIndexEntry = addLabeledSliderAndEntry(leftLayout, "Slice Index (Int): ", 
                                                                                         QIntValidator(0, 0), 
                                                                                         self.updateBasisView)
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
            if self.doBackgroundScansMarker.isChecked():
                self.roiVizButtonGroup.buttons()[1].setEnabled(True)
        if index == 1:
            self.log(f"did we get updated ROI? {self.ROI.updated}")
            if self.ROI.updated:
                self.shimTool.recomputeCurrentsAndView()
            self.ROI.updated = False
        if index == 2:
            self.updateBasisView()

    def updateLogOutput(self, log, text):
        log.append(text)

    def setView(self, qImage: QImage, view: ImageViewer, ROI:bool=False):
        """Sets the view of the ImageViewer to the given QImage."""
        pixmap = QPixmap.fromImage(qImage)
        if not ROI:
            view.qImage = qImage
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
        if self.doBackgroundScansMarker.isChecked():
            self.roiVizButtonGroup.buttons()[1].setEnabled(True)

    def updateDisplay(self, viewIndex):
        """Update a specific view with the corresponding underlying data available."""
        viewDataSlice = self.viewDataSlice[viewIndex] # this should be a 2d numpy array now
        # if view data is not none, then so should the slice and maxAbs value
        if viewDataSlice is not None:
            # Extract the slice and normalize it
            normalizedData = (viewDataSlice - np.nanmin(viewDataSlice)) / (2*self.viewMaxAbs[viewIndex]) * 255
            # specific pyqt6 stuff to convert numpy array to QImage
            displayData = np.ascontiguousarray(normalizedData).astype(np.uint8)
            # make the value 127 (correlates to 0 Hz offset) wherever it is outside of mask
            displayData[np.isnan(viewDataSlice)] = 127
            # stack 4 times for R, G, B, and alpha value
            rgbData = np.stack((displayData,)*3, axis=-1)
            height, width, _ = rgbData.shape
            bytesPerLine = rgbData.strides[0] 
            qImage = QImage(rgbData.data, width, height, bytesPerLine, QImage.Format.Format_RGB888)
            # set the actual view that we care about
            self.setView(qImage, self.views[viewIndex])
        
    def visualizeROI(self):
        """
        Visualize the ROI based on the current selected shape type slider values.
        """
        if not self.ROI or not self.views[0].qImage:
            raise GuiError("ROI object or qImage not already initialized and trying to visualize ROI")
        if not self.ROI.enabled or self.roiVizButtonGroup.checkedId() == 0 or not self.doBackgroundScansMarker.isChecked():
            return # dont do anything because either the toggle is not enabled,

        # TODO: add support for other ROI shapes and selection here
        self.ROI = self.ROI

        old = self.ROI.sizes + self.ROI.centers

        # Update the ROI values based on the sliders
        for i in range(3):
            self.log(f"for idx {i} the slider sizes: {self.roiSizeSliders[i].value()}, the slider centers: {self.roiPositionSliders[i].value()}")
            self.ROI.sliderSizes[i] = self.roiSizeSliders[i].value() / self.roiSliderGranularity
            self.ROI.sliderCenters[i] = self.roiPositionSliders[i].value() / self.roiSliderGranularity
        
        qImage = self.views[0].qImage
        depth = self.viewData[0].shape[0]

        # scale relative to the image size
        self.ROI.sizes[0] = max(1, round((self.ROI.xdim // 2) * self.ROI.sliderSizes[0]))
        self.ROI.sizes[1] = max(1, round((self.ROI.ydim // 2) * self.ROI.sliderSizes[1]))
        self.ROI.sizes[2] = max(1, round((self.ROI.zdim // 2) * self.ROI.sliderSizes[2]))
        self.ROI.centers[0] = round(self.ROI.xdim * self.ROI.sliderCenters[0])
        self.ROI.centers[1] = round(self.ROI.ydim * self.ROI.sliderCenters[1])
        self.ROI.centers[2] = round(self.ROI.zdim * self.ROI.sliderCenters[2])

        self.log(f"the ROI sizes: {self.ROI.sizes}, the centers: {self.ROI.centers}")
        
        # check if the ROI has been updated
        if old != self.ROI.sizes + self.ROI.centers:
            self.ROI.updated = True

        sliceIdx = self.roiSliceIndexSlider.value()
        offsetFromDepthCenter = abs(sliceIdx - self.ROI.centers[2])
        self.log(f"Offset from depth center: {offsetFromDepthCenter}")
        if offsetFromDepthCenter <= self.ROI.sizes[2]:
            self.log(f"abt to try drawing points")
            drawingQImage = qImage.copy()
            painter = QPainter(drawingQImage)
            # Set the pen color to red and the brush to a semi transparent red
            painter.setPen(QPen(QBrush(QColor(255, 0, 0, 100)), 1))
            points = self.ROI.getSlicePoints(sliceIdx)
            for x, y in points:
                painter.drawPoint(x, y)
            painter.end()
        
            self.setView(drawingQImage, self.views[0], ROI=True)
    
    def validateROIInput(self):
        """Validate the ROI input sliders and entries."""
        # check that there is some data in viewData before trying this
        if self.viewData[0] is None:
            self.log("No data available to visualize ROI.")
            return False

        # there is reason to enable the slice index slider now, if it wasn't already
        self.roiSliceIndexSlider.setEnabled(True)
        self.roiSliceIndexEntry.setEnabled(True)
        # the data should have already been placed into viewData

        # get the max abs value of the background data
        self.viewMaxAbs[0] = np.max(np.abs(self.viewData[0]))
        # if the data is background
        if self.roiVizButtonGroup.checkedId() == 1:
            if not self.doBackgroundScansMarker.isChecked():
                raise GuiError("Background scans not yet obtained.")
            # need to set the slider limits
            upperlimit = self.viewData[0].shape[1]-1
            updateSliderEntryLimits(self.roiSliceIndexSlider, self.roiSliceIndexEntry, 
                                    0, upperlimit, QIntValidator(0, upperlimit))
                                    # assume that slider value automatically updated to be within bounds
            # need to set viewDataSlice to the desired slice
            self.viewDataSlice[0] = self.viewData[0][:,self.roiSliceIndexSlider.value(),:]
            # set ROI limits TODO issue #1
            ydim, zdim, xdim = self.viewData[0].shape
            self.ROI.setROILimits(xdim, ydim, zdim)
        # if the data is latest data
        else:
            upperlimit = self.viewData[0].shape[0]-1
            updateSliderEntryLimits(self.roiSliceIndexSlider, self.roiSliceIndexEntry, 
                                    0, upperlimit, QIntValidator(0, upperlimit))
                                    # assume that slider value automatically updated to be within bounds
            # need to set viewDataSlice to the desired slice
            self.viewDataSlice[0] = self.viewData[0][self.roiSliceIndexSlider.value()]
        return True

    def updateROIImageDisplay(self):
        """Update the ROI image display based on the current slice index."""
        # validate the ranges and set the sliders enabled
        if self.validateROIInput():
            self.updateDisplay(0)
            self.visualizeROI()

    def toggleROIBackgroundImage(self):
        if self.roiVizButtonGroup.checkedId() == 1 and self.doBackgroundScansMarker.isChecked():
            self.roiToggleButton.setEnabled(True)

    def toggleROIEditor(self):
        if self.roiVizButtonGroup.checkedId() == 1:
            if self.ROI.enabled:
                self.ROI.enabled = False
                self.roiToggleButton.setText("Enable ROI Editor")
                self.shimTool.roiMask = None
            else:
                self.ROI.enabled = True
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
        if self.viewData[1][selectedShimView] is None:
            return False

        # get the max abs value of the shimView data set        
        for i in range(self.viewData[1].shape[0]):
            if self.viewData[1][i] is not None:
                self.viewMaxAbs[1] = max(self.viewMaxAbs[1], np.nanmax(np.abs(self.viewData[1][i])))

        # set the limit to the shim slice index slider 
        upperlimit = self.viewData[1][0].shape[1]-1
        updateSliderEntryLimits(self.shimSliceIndexSlider, self.shimSliceIndexEntry,
                                0, upperlimit, QIntValidator(0, upperlimit))
                                # assume that slider value automatically updated to be within bounds
        # there is reason to enable the slice index slider now, if it wasn't already
        self.shimSliceIndexSlider.setEnabled(True)
        self.shimSliceIndexEntry.setEnabled(True)
        # set the viewDataSlice to the desired slice from the whole 4D data set
        self.viewDataSlice[1] = self.viewData[1][selectedShimView][:,self.shimSliceIndexSlider.value(),:]
        return True
    
    def updateShimStats(self):
        """Update the shim statistics text boxes."""
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
        shimtxt = ""
        if self.shimTool.ogLinShimValues is not None:
            shimtxt += f"Default lin gradient shims = {self.shimTool.ogLinShimValues}"
        if self.exsiInstance.ogCenterFreq is not None:
            shimtxt += f" | OG CF = {self.exsiInstance.ogCenterFreq} Hz"
        self.doShimProcedureLabel.setText(f"SHIM OPERATIONS; " + shimtxt)

        # if currents are available
        if self.shimTool.solutionValuesToApply is not None:
            if self.shimTool.solutionValuesToApply[sliceIndex] is not None:
                solutions = self.shimTool.solutionValuesToApply[sliceIndex]
                text = f"Δcf:{int(round(solutions[0]))}|"
                numIter = self.shimInstance.numLoops + 3
                pref = ["X", "Y", "Z"]
                for i in range(numIter):
                    if i < 3:
                        text += 'g' + pref[i]
                        text += f":{int(round(solutions[i+1]))}|"
                    else:
                        text += f"ch{i-3}:{solutions[i+1]:.2f}|"
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
        for i in range(self.viewData[2].shape[0]):
            if self.viewData[2][i] is not None:
                self.viewMaxAbs[2] = max(self.viewMaxAbs[2], np.nanmax(np.abs(self.viewData[2][i])))
            else:
                self.log("ERROR: Calibration scans done marker checked, but basis data is not available yet")
                return False # cancel the viewing, since clearly some of the data is not there yet...

        # set the limit to the basis slice index slider
        numslices = self.viewData[2][0].shape[1]-1
        updateSliderEntryLimits(self.basisSliceIndexSlider, self.basisSliceIndexEntry,
                                0, numslices, QIntValidator(0, numslices))

        # there is reason to enable the slice index slider now, if it wasn't already
        self.basisSliceIndexSlider.setEnabled(True)
        self.basisSliceIndexEntry.setEnabled(True)

        # set the viewDataSlice to the desired slice from the whole 4D data set
        self.viewDataSlice[2] = self.viewData[2][self.basisFunctionSlider.value()][:,self.basisSliceIndexSlider.value(),:]
        return True

    def updateBasisView(self):
        """Update the basis function image display based on the current slice index and basis function index."""
        if self.doLoopCalibrationScansMarker.isChecked() and self.validateBasisInputs():
            self.updateDisplay(2)
            # TODO update histograms on the right.

    def log(self, msg):
        header = "GUI: "
        log(header + msg, self.debugging)
 

class GuiError(Exception):
    """Custom exception for GUI errors."""
    pass