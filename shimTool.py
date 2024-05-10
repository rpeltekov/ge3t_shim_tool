"""The Shim Tool Object for orchestrating the shim process."""


from datetime import datetime
import warnings
import sys, os, threading
import numpy as np
import json
from typing import List

# Import the custom client classes and util functions
from exsi_client import exsi
from shim_client import shim
from dicomUtils import *
from shimCompute import *
from utils import *
from guiUtils import *
from gui import *


class shimTool():

    def __init__(self, config: dict):
        
        # ----------- Shim Tool Essential Attributes ----------- #
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

        # ----------- Clients ----------- #
        # Start the connection to the Shim client.
        # the requireShimConnection decorator will check if the connection is ready before executing any shim functionality.
        self.shimInstance = shim(self.config['shimPort'], self.config['shimBaudRate'], self.shimLog, debugging=self.debugging)

        # Start the connection to the scanner client.
        # The requireExsiConnection decorator will check if the connection is ready before executing any exsi functionality.
        self.exsiInstance = exsi(self.config['host'], self.config['exsiPort'], self.config['exsiProduct'], self.config['exsiPasswd'],
                                 self.shimInstance.shimZero, self.shimInstance.shimSetCurrentManual, self.scannerLog, debugging=self.debugging)
        
        # connect the clear queue commands so that they can be called from the other client
        self.shimInstance.clearExsiQueue = self.exsiInstance.clear_command_queue
        self.exsiInstance.clearShimQueue = self.shimInstance.clearCommandQueue

        # main GUI instantiation
        self.app = QApplication(sys.argv)
        self.gui = Gui(self.debugging, self, self.exsiInstance, self.shimInstance, self.scannerLog, self.shimLog)


        # ----------- Shim Tool Parameters ----------- #
        self.maxDeltaTE = 2000 # 2000 us = 2 ms
        self.minDeltaTE = 100 # 2000 us = 2 ms
        self.minCalibrationCurrent = 100 # 100 mA
        self.maxCalibrationCurrent = 2000 # 2 A
        self.linShimFactor = 20 # max 300 -- the value at which to record basis map for lin shims
        self.ogLinShimValues = None # the original linear shim values

        self.gehcExamDataPath = None # the path to the exam data on the GE Server
        self.localExamRootDir = None # Where the raw dicom data gets stored, once pulled from the GE Server
        self.backgroundDCMdir = None # the specific local Dicom Directory for the background image 
 
        # ----------- Shim Tool State ----------- #
        # Scan session attributes
        self.assetCalibrationDone = False
        self.autoPrescanDone = False
        self.obtainedBasisMaps = False
        self.computedShimSolutions = False

        self.roiEditorEnabled = False

        # 3d data arrays
        self.backgroundB0Map: np.ndarray = None # 3d array of the background b0 map 
        self.rawBasisB0maps: List[np.ndarray] = [None for _ in range(self.shimInstance.numLoops + 3)] # 3d arrays of the basis b0 maps with background
        self.basisB0maps: List[np.ndarray] = [None for _ in range(self.shimInstance.numLoops + 3)] # 3d arrays of the basis b0 maps without background
        self.expectedB0Map: np.ndarray = None # 3d array of the shimmed b0 map; Shimming is slice-wise -> i.e. one slice is filled at a time per solution
        self.shimmedB0Map: np.ndarray = None # 3d array of the shimmed b0 map; Shimming is slice-wise -> i.e. one slice is filled at a time

        # solution constants; solutions based on the basis maps, not necessary the actual current / Hz / lingrad values
        self.solutions: List[np.ndarray] = None
        # the actual values that will be used to apply the shim currents/ cf offset / lingrad value
        self.solutionValuesToApply: List[np.ndarray] = None # will be in form of Hz, number for the lingrad, and Amps

        # masks
        self.roiMask: np.ndarray = None # 3d boolean mask in the same shape as the background data
        self.finalMask: np.ndarray = None # the intersection of roi, and all nonNan sections of background and basis maps

        # the stat string outputs
        self.shimStatStrs: List[List[str]] = [None, None, None] # string form stats
        self.shimStats: List[List] = [None, None, None] # numerical form stats


    def run(self):
        # start the gui
        self.gui.show()

        # wait for exsi connected to update the name of the GUI application, the tab, and to create the exam data directory
        def waitForExSIConnectedReadyEvent():
            if not self.exsiInstance.connected_ready_event.is_set():
                self.exsiInstance.connected_ready_event.wait()
            self.localExamRootDir = os.path.join(self.config['rootDir'], "data", self.exsiInstance.examNumber)
            if not os.path.exists(self.localExamRootDir):
                os.makedirs(self.localExamRootDir)
            self.gui.setWindowAndExamNumber(self.exsiInstance.examNumber, self.exsiInstance.patientName)
            self.gui.renameTab(self.exsiTab, "ExSI Control")
        # wait for the shim drivers connected to update the tab name
        def waitForShimConnectedEvent():
            if not self.shimInstance.connectedEvent.is_set():
                self.shimInstance.connectedEvent.wait()
            self.gui.renameTab(self.shimTab, "Shim Control")
    
        # let us hope that there isn't some race condition here...
        kickoff_thread(waitForExSIConnectedReadyEvent)
        kickoff_thread(waitForShimConnectedEvent)

        # start the PyQt event loop
        sys.exit(self.app.exec())

    # ----------- Shim Tool Helper Functions ----------- #

    def transferScanData(self):
        self.log(f"Debug: initiating transfer using rsync.")
        if self.exsiInstance.examNumber is None:
            self.log("Error: No exam number found in the exsi client instance.")
            return
        if self.gehcExamDataPath is None:
            self.gehcExamDataPath = setGehcExamDataPath(self.exsiInstance.examNumber, self.config['host'], self.config['hvPort'], self.config['hvUser'], self.config['hvPassword'])
            self.log(f"Debug: obtained exam data path: {self.gehcExamDataPath}")
        execRsyncCommand(self.config['hvPassword'], self.config['hvUser'], self.config['host'], self.gehcExamDataPath + '/*', self.localExamRootDir)

    def getLatestData(self, stride=1, offset=0):
        latestDCMDir = listSubDirs(self.localExamRootDir)[-1]
        res = extractBasicImageData(latestDCMDir, stride, offset)
        self.gui.viewData[0] = res[0]
    
    def getROIBackgound(self):
        self.log('Debug: extracting the background mag image')
        res = extractBasicImageData(self.backgroundDCMdir, stride=3, offset=0)
        self.log('Debug: done extracting the background mag image')
        self.gui.viewData[0] = res[0]


    # ----------- Shim Functions ----------- #

    def setLinGradients(self, linGrad):
        """Set the new gradient as offset from the prescan set ones"""
        linGrad = linGrad + self.ogLinShimValues
        self.exsiInstance.sendSetShimValues(*linGrad)

    def sendSyncedShimCurrent(self, channel: int, current: float):
        """Send a shim loop set current command, but via the ExSI client 
        to ensure that the commands are synced with other exsi commands."""
        # TODO: adjust for multiple boards
        self.exsiInstance.send(f"X {channel} {current}")

    def queueBasisPairScanDetails(self, linGrad=None, preset=False):
        """
        once the b0map sequence is loaded, subroutines are iterated along with cvs to obtain basis maps.
        linGrad should be a list of 3 floats if it is not None
        """
        cvs = {"act_tr": 3300, "act_te": [1104, 1604], "rhrcctrl": 13, "rhimsize": 64}
        for i in range(2):
            self.exsiInstance.sendSelTask()
            self.exsiInstance.sendActTask()
            if linGrad:
                self.setLinGradients(linGrad)
            elif not preset:
                self.setLinGradients(np.array([0,0,0]))
            for cv in cvs.keys():
                if cv == "act_te":
                    if i == 0:
                        self.exsiInstance.sendSetCV(cv, cvs[cv][0])
                    else:
                        self.exsiInstance.sendSetCV(cv, cvs[cv][0] + self.shimDeltaTE)
                else:
                    self.exsiInstance.sendSetCV(cv, cvs[cv])
            self.exsiInstance.sendPatientTable()
            if not self.autoPrescanDone:
                self.exsiInstance.sendPrescan(auto=True)
                self.autoPrescanDone = True
            else:
                self.exsiInstance.sendPrescan(auto=False)
            self.exsiInstance.sendScan()

    def queueBasisPairScan(self, linGrad=None, preset=False):
        # Basic basis pair scan. should be used to scan the background
        self.exsiInstance.sendLoadProtocol("ConformalShimCalibration3")
        self.queueBasisPairScanDetails(linGrad, preset)

    def queueCaliBasisPairScan(self, channelNum, nonDefaultCurrent=None):
        if not nonDefaultCurrent:
            nonDefaultCurrent = self.gui.getShimCalCurrent()
        # when the exsiclient gets this specific command, it will know to dispatch both the loadProtocol 
        # command and also a Zero Current and setCurrent to channelNum with calibration current of 1.0
        self.exsiInstance.send(f'LoadProtocol site path="ConformalShimCalibration3" | {channelNum} {nonDefaultCurrent}')
        self.queueBasisPairScanDetails()
        self.log(f"DEBUG: DONE queueing basis paid scan!")

    def countScansCompleted(self, n):
        """should be 2 for every basis pair scan"""
        for i in range(n):
            self.log(f"DEBUG: checking to see if failure happened on last run: currently on scan {i+1} / {n}")
            if not self.exsiInstance.no_failures.is_set():
                self.log("Error: scan failed")
                self.exsiInstance.no_failures.set()
                return False
            self.log(f"DEBUG: Waiting for scan to complete, currently on scan {i+1} / {n}")
            if not self.exsiInstance.images_ready_event.wait(timeout=90):
                self.log(f"Error: scan {i+1} / {n} didn't complete within 90 seconds bruh")
                return False
            else:
                self.exsiInstance.images_ready_event.clear()
                # TODO probably should raise some sorta error here...
        self.log(f"DEBUG: {n} scans completed!")
        # after scans get completed, go ahead and get the latest scan data over on this machine...
        self.transferScanData()
        return True

    def saveROIMask(self):
        # make a mask the same shape as self.shimImages[0] based on the ellipsoid parameters
        if self.roiEditorEnabled:
            self.log(f"DEBUG: Saving ROI mask")
            self.roiMask = np.zeros_like(self.shimImages[0], dtype=bool)
        else:
            self.roiMask = None
        self.computeMask()

    def triggerComputeShimCurrents(self):
        """if background and basis maps are obtained, compute the shim currents"""
        if self.gui.doBackgroundScansMarker.isChecked() and self.gui.doLoopCalibrationScansMarker.isChecked():
            self.expectedB0Map = None
            self.computeShimCurrents()
        elif self.gui.doBackgroundScansMarker.isChecked():
            self.computeMask()
        self.evaluateShimImages()

    def getSolutionsToApply(self):
        """From the Solutions, set the actual values that will be applied to the shim system."""
        # cf does not change.
        self.solutionValuesToApply = [np.copy(self.solutions[i]) for i in range(len(self.solutions))]
        for i in range(len(self.solutions)):
            for j in range(1,4):
                # update the lingrad values
                self.solutionValuesToApply[i][j] = self.solutionValuesToApply[i][j] * self.linShimFactor
            for j in range(4, len(self.solutions[i])):
                # update the current values
                self.solutionValuesToApply[i][j] = self.solutionValuesToApply[i][j] * self.gui.getShimCalCurrent()


    # ----------- Shim Tool Compute Functions ----------- #

    def computeMask(self):
        """compute the mask for the shim images."""
        self.finalMask = createMask(self.backgroundB0Map, self.basisB0maps, self.roiMask)

    def computeBackgroundB0map(self):
        # assumes that you have just gotten background by queueBasisPairScan
        b0maps = compute_b0maps(1, self.localExamRootDir)
        self.backgroundDCMdir = listSubDirs(self.localExamRootDir)[-1]
        self.backgroundB0Map = b0maps[0]

    def computeBasisB0maps(self):
        # assumes that you have just gotten background by queueBasisPairScan
        if self.gui.withLinGradMarker.isChecked():
            self.rawBasisB0maps = compute_b0maps(self.shimInstance.numLoops + 3, self.localExamRootDir)
        else:
            self.rawBasisB0maps = compute_b0maps(self.shimInstance.numLoops, self.localExamRootDir)
    
    def computeShimCurrents(self):
        """
        Compute the optimal solutions (currents and lin gradients and cf) for every slice
        Save the generated expected B0 map to the expectedB0map array
        """
        # run whenever both backgroundB0Map and basisB0maps are computed or if one new one is obtained
        self.basisB0maps = subtractBackground(self.backgroundB0Map, self.rawBasisB0maps)
        self.computeMask()

        self.solutions = [None for _ in range(self.backgroundB0Map.shape[1])]
        for i in range(self.backgroundB0Map.shape[1]):
            # want to include slice in front and behind in the mask when solving currents though:
            mask = maskOneSlice(self.finalMask, i)
            if i > 0:
                mask = np.logical_or(mask, maskOneSlice(self.finalMask, i-1))
            if i < self.backgroundB0Map.shape[1] - 1:
                mask = np.logical_or(mask, maskOneSlice(self.finalMask, i+1))
            #NOTE: the first and last current that is solved will be for an empty slice...
            self.solutions[i] = solveCurrents(self.backgroundB0Map, 
                                             self.basisB0maps, mask, 
                                             withLinGrad=True, 
                                             linShimFactor=self.linShimFactor,
                                             calibrationCurrent=self.gui.getShimCalCurrent(),
                                             debug=self.debugging)

        self.getSolutionsToApply() # compute the actual values we will apply to the shim system from these solutions

        # if not all currents are none
        if not all([c is None for c in self.solutions]):
            self.expectedB0Map = self.backgroundB0Map.copy()
            for i in range(self.backgroundB0Map.shape[1]):
                if self.solutions[i] is not None:
                    self.expectedB0Map[:,i,:] += self.solutions[i][0] * np.ones(self.shimData[0].shape)
                    numIter = self.shimInstance.numLoops + 3
                    for j in range(numIter):
                        # self.log(f"DEBUG: adding current {j} to shimData[1][{i}]")
                        self.expectedB0Map[:,i,:] += self.solutions[i][j+1] * self.basisB0maps[j]
                else:
                    self.shimData[:,i,:] = np.nan
            self.gui.currentsComputedMarker.setChecked(True)
        else:
            self.log("Error: Could not solve for currents. Look at error hopefully in output")

    def computeShimmedB0Map(self, idx = None):
        """Compute the just obtained b0map of the shimmed background, for the specific slice selected"""
        b0maps = compute_b0maps(1, self.localExamRootDir)
        if self.shimmedB0Map is None:
            self.shimmedB0Map = np.full_like(b0maps[0], np.nan)

        if idx is not None:
            self.shimmedB0Map[:,idx,:] = b0maps[0][:,idx,:]
        else:
            idx = self.gui.getShimSliceIndex()
            self.shimmedB0Map[:,idx,:] = b0maps[0][:,idx,:]
    
    def evaluateShimImages(self):
        """evaluate the shim images (with the final mask applied) and store the stats in the stats array."""
        for i in range(self.backgroundB0Map.shape[1]):
            self.shimStatStrs[i] = [None for _ in range(self.backgroundB0Map.shape[1])]
            self.shimStats[i]  = [None for _ in range(self.backgroundB0Map.shape[1])]
            mask = maskOneSlice(self.finalMask, i)
            for map, j in enumerate([self.backgroundB0Map, self.expectedB0Map, self.shimmedB0Map]):
                if not np.isnan(map[mask]).all():
                    statsstr, stats = evaluate(map[mask], self.debugging)
                    self.shimStatStrs[j][i] = statsstr
                    self.shimStats[j][i] = stats
    
    def evaluateAppliedShims(self):
        """
        Compare the expected vs actual performance of every shim loop / linear gradient / CF offset and save the difference.
        Helpful to evaluate if the solutions are actually what is being applied.
        """
        b0maps = compute_b0maps(self.shimInstance.numLoops + 4, self.localExamRootDir)
        for i in range(len(b0maps)):
            # save actual b0map to an eval folder, within which it says the current slice that we are on...
            sliceIdx = self.gui.getShimSliceIndex()
            # save the b0map to the eval folder
            evalDir = os.path.join(self.config['rootDir'], "results", self.exsiInstance.examNumber, "eval", f"slice_{sliceIdx}")
            if not os.path.exists(evalDir):
                os.makedirs(evalDir)
            np.save(os.path.join(evalDir, f"b0map{i}.npy"), b0maps[i][:,sliceIdx,:])
            # compute the difference from the expected b0map
            expected = np.copy(self.backgroundB0Map[:,sliceIdx,:])
            if i == 0:
                expected += self.solutions[sliceIdx][i] * np.ones(expected.shape)
            else:
                expected += self.solutions[sliceIdx][i] * self.basisB0maps[i][:,sliceIdx,:]

            np.save(os.path.join(evalDir, f"expected{i}.npy"), expected)

            difference = b0maps[i][:,sliceIdx,:] - expected
            fig, ax = plt.subplots(figsize=(8, 6))
            im = ax.imshow(difference, cmap='jet', vmin=-100, vmax=100)
            cbar = plt.colorbar(im)

            plt.title(f"difference basis{i}, slice{sliceIdx}", size=10)
            plt.axis('off')
            
            fig.savefig(os.path.join(evalDir, f"difference{i}.png"), bbox_inches='tight', transparent=False)
            plt.close(fig)


    # ----------- Shim Tool Button Functions ----------- #

    def onTabSwitch(self, index):
        self.log(f"DEBUG: Switched to tab {index}")
        if index == 0:
            self.gui.updateROIImageDisplay()
        if index == 1:
            if self.gui.ROI.updated:
                self.roiMask = self.gui.ROI.getROIMask()
                self.recomputeCurrentsAndView()
            self.gui.ROI.updated = False
        if index == 2:
            self.gui.updateBasisView()

    def shimSetManualCurrent(self):
        """Set the shim current to the value in the entry."""
        channel = int(self.gui.shimManualChannelEntry.text())
        current = float(self.gui.shimManualCurrenEntry.text())
        self.shimInstance.shimSetCurrentManual(channel, current)


    @disableSlowButtonsTillDone
    def calibrationScanWork(self, trigger: Trigger):
        self.exsiInstance.sendLoadProtocol("ConformalShimCalibration4")
        self.exsiInstance.sendSelTask()
        self.exsiInstance.sendActTask()
        self.exsiInstance.sendPatientTable()
        self.exsiInstance.sendScan()
        if self.exsiInstance.images_ready_event.wait(timeout=120):
            self.assetCalibrationDone = True
            self.exsiInstance.images_ready_event.clear()
            self.transferScanData()
            self.getLatestData(stride=1)
        trigger.finished.emit()
    @requireExsiConnection
    def doCalibrationScan(self):
        # dont need to do the assetCalibration scan more than once
        if not self.exsiInstance or self.assetCalibrationDone:
            return
        trigger = Trigger()
        trigger.finished.connect(self.gui.updateROIImageDisplay)
        kickoff_thread(self.calibrationScanWork, args=(trigger,))
    

    @disableSlowButtonsTillDone
    def fgreScanWork(self, trigger: Trigger):
        self.exsiInstance.sendLoadProtocol("ConformalShimCalibration5")
        self.exsiInstance.sendSelTask()
        self.exsiInstance.sendActTask()
        self.exsiInstance.sendPatientTable()
        self.exsiInstance.sendScan()
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
        trigger.finished.connect(self.gui.updateROIImageDisplay)
        kickoff_thread(self.fgreScanWork, args=(trigger,))
 

    @disableSlowButtonsTillDone
    def getAndSetROIImageWork(self, trigger: Trigger):
        if self.gui.getROIBackgroundSelected() == 1:
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
        trigger = Trigger()
        trigger.finished.connect(self.gui.updateROIImageDisplay)
        kickoff_thread(self.getAndSetROIImageWork, args=(trigger,))
   

    @disableSlowButtonsTillDone
    def recomputeCurrentsAndView(self):
        self.triggerComputeShimCurrents()
        self.gui.updateShimImageAndStats()


    @disableSlowButtonsTillDone
    def waitBackgroundScan(self, trigger: Trigger):
        self.gui.doBackgroundScansMarker.setChecked(False)
        self.shimInstance.shimZero() # NOTE(rob): Hopefully this zeros quicker that the scans get set up...
        self.backgroundB0Map = None
        self.exsiInstance.images_ready_event.clear()
        if self.countScansCompleted(2):
            self.gui.roiVizButtonGroup.buttons()[1].setEnabled(True)
            self.transferScanData()
            self.log("DEBUG: just finished all the background scans")
            self.computeBackgroundB0map()
            # if this is a new background scan and basis maps were obtained, then compute the shim currents
            self.gui.doBackgroundScansMarker.setChecked(True)
            self.exsiInstance.send("GetPrescanValues") # get the center frequency
            self.triggerComputeShimCurrents()
            self.ogLinShimValues = getLastSetGradients(self.config['host'], self.config['hvPort'], self.config['hvUser'], self.config['hvPassword'])
        else:
            self.log("Error: Scans didn't complete")
            self.exsiInstance.images_ready_event.clear()
            self.exsiInstance.ready_event.clear()
        trigger.finished.emit()
    @requireExsiConnection
    @requireShimConnection
    @requireAssetCalibration
    def doBackgroundScans(self):
        # Perform the background scans for the shim system.
        trigger = Trigger()
        def updateVals():
            self.gui.updateShimImageAndStats()
        trigger.finished.connect(updateVals)
        kickoff_thread(self.waitBackgroundScan, args=(trigger,))
        self.queueBasisPairScan()


    @disableSlowButtonsTillDone
    def waitLoopCalibrtationScan(self, trigger: Trigger):
        self.gui.doLoopCalibrationScansMarker.setChecked(False)
        self.shimInstance.shimZero() # NOTE: Hopefully this zeros quicker that the scans get set up...
        self.rawBasisB0maps = None
        self.exsiInstance.images_ready_event.clear()
        num_scans = (self.shimInstance.numLoops + (3 if self.gui.withLinGradMarker.isChecked() else 0)) * 2
        if self.countScansCompleted(num_scans):
            self.log("DEBUG: just finished all the calibration scans")
            self.computeBasisB0maps()
            # if this is a new background scan and basis maps were obtained, then compute the shim currents
            self.gui.doLoopCalibrationScansMarker.setChecked(True)
            self.triggerComputeShimCurrents()
        else:
            self.log("Error: Scans didn't complete")
            self.exsiInstance.images_ready_event.clear()
            self.exsiInstance.ready_event.clear()
        trigger.finished.emit()
    @requireExsiConnection
    @requireShimConnection
    @requireAssetCalibration
    def doLoopCalibrationScans(self):
        """Perform all the calibration scans for each loop in the shim system."""
        trigger = Trigger()
        trigger.finished.connect(self.gui.updateShimImageAndStats)
        kickoff_thread(self.waitLoopCalibrtationScan, args=(trigger,))
        def queueAll():
            # perform the calibration scans for the linear gradients
            if self.gui.withLinGradMarker.isChecked():
                self.queueBasisPairScan(np.array([self.linShimFactor,0,0]))
                self.queueBasisPairScan(np.array([0,self.linShimFactor,0]))
                self.queueBasisPairScan(np.array([0,0,self.linShimFactor]))
            for i in range(self.shimInstance.numLoops):
                self.queueCaliBasisPairScan(i)
        kickoff_thread(queueAll)


    @requireShimConnection
    def shimSetAllCurrents(self, sliceIdx=None):
        if not self.gui.currentsComputedMarker.isChecked() or not self.solutions:
            self.log("Debug: Need to perform background and loop calibration scans before setting currents.")
            msg = createMessageBox("Error: Background And Loop Cal Scans not Done",
                                   "Need to perform background and loop calibration scans before setting currents.", 
                                   "You could set them manually if you wish to.")
            msg.exec() 
            return # do nothing more

        self.gui.setAllCurrentsMarker.setChecked(False)
        if sliceIdx is None:
            sliceIdx = self.gui.getShimSliceIndex()
        if self.solutions[sliceIdx] is not None:
            # setting center frequency
            newCenterFreq = int(self.exsiInstance.ogCenterFreq) + int(round(self.solutionValuesToApply[sliceIdx][0]))
            self.log(f"DEBUG: Setting center frequency from {self.exsiInstance.ogCenterFreq} to {newCenterFreq}")
            self.exsiInstance.sendSetCenterFrequency(newCenterFreq)

            # setting the linear shims
            if self.gui.withLinGradMarker.isChecked():
                linGrads = self.solutionValuesToApply[sliceIdx][1:4]
                linGrads = np.round(linGrads).astype(int)
                self.setLinGradients(linGrads)
                
            # setting the loop shim currents
            for i in range(self.shimInstance.numLoops):
                current, solution = 0.0, 0.0
                if self.gui.withLinGradMarker.isChecked():
                    current = self.solutionValuesToApply[sliceIdx][i+4]
                    solution = self.solutions[sliceIdx][i+4]
                else:
                    current = self.solutionValuesToApply[sliceIdx][i+1]
                    solution = self.solutions[sliceIdx][i+1]
                self.log(f"DEBUG: Setting currents for loop {i} to {current:.3f}, bc of solution {solution:.3f}")
                self.sendSyncedShimCurrent(i%8, current)
        self.gui.setAllCurrentsMarker.setChecked(True)


    @disableSlowButtonsTillDone
    def waitShimmedScans(self, trigger: Trigger):
        self.gui.doShimmedScansMarker.setChecked(False)
        self.shimmedB0Map[:,self.gui.getShimSliceIndex,:] = np.nan
        self.exsiInstance.images_ready_event.clear()
        if self.countScansCompleted(2):
            self.computeShimmedB0Map()
            self.evaluateShimImages()
            self.gui.doShimmedScansMarker.setChecked(True)
        else:
            self.log("Error: Scans didn't complete")
            self.exsiInstance.images_ready_event.clear()
            self.exsiInstance.ready_event.clear()
        trigger.finished.emit()
    @requireExsiConnection
    @requireShimConnection
    @requireAssetCalibration
    def doShimmedScans(self):
        """ Perform another set of scans now that it is shimmed """
        if not self.gui.setAllCurrentsMarker.isChecked():
                msg = createMessageBox("Note: Shim Process Not Performed",
                                       "If you want correct shims, click above buttons and redo.", "")
                msg.exec() 
                return
        trigger = Trigger()
        trigger.finished.connect(self.gui.updateShimImageAndStats)
        kickoff_thread(self.waitShimmedScans, args=(trigger,))
        self.queueBasisPairScan(preset=True)


    @disableSlowButtonsTillDone
    def waitdoEvalAppliedShims(self, trigger: Trigger):
        self.shimInstance.shimZero()
        self.exsiInstance.images_ready_event.clear()
        num_scans = (self.shimInstance.numLoops + (4 if self.gui.withLinGradMarker.isChecked() else 0)) * 2
        if self.countScansCompleted(num_scans):
            self.log("DEBUG: just finished all the shim eval scans")
            self.evaluateAppliedShims()
            self.triggerComputeShimCurrents()
        else:
            self.log("Error: Scans didn't complete")
            self.exsiInstance.images_ready_event.clear()
            self.exsiInstance.ready_event.clear()
        trigger.finished.emit()
    @requireExsiConnection
    @requireShimConnection
    @requireAssetCalibration
    def doEvalAppliedShims(self):
        """Scan with supposed set shims and evaluate how far from expected they are."""
        trigger = Trigger()
        trigger.finished.connect(self.gui.updateShimImageAndStats)
        kickoff_thread(self.waitdoEvalAppliedShims, args=(trigger,))
        def queueAll():
            # perform the calibration scans for the linear gradients
            apply = self.solutionValuesToApply[self.gui.getShimSliceIndex()]
            newCF = int(self.exsiInstance.ogCenterFreq) + int(round(apply[0]))
            self.exsiInstance.sendSetCenterFrequency(newCF)
            self.queueBasisPairScan()

            self.exsiInstance.sendSetCenterFrequency(int(self.exsiInstance.ogCenterFreq))
            if self.gui.withLinGradMarker.isChecked():
                self.queueBasisPairScan(np.array([int(round(apply[1])), 0, 0]))
                self.queueBasisPairScan(np.array([0, int(round(apply[1])), 0]))
                self.queueBasisPairScan(np.array([0, 0, int(round(apply[1]))]))

            for i in range(self.shimInstance.numLoops):
                self.queueCaliBasisPairScan(i, apply[i+4])
        kickoff_thread(queueAll)


    @disableSlowButtonsTillDone
    def waitdoAllShimmedScans(self, trigger: Trigger, start, numindex):
        self.gui.doAllShimmedScansMarker.setChecked(False)
        self.exsiInstance.images_ready_event.clear()

        for idx in range(start, start+numindex):
            self.log(f"-------------------------------------------------------------")
            self.log(f"DEBUG: STARTING B0MAP {idx-start+1} / {numindex}; slice {idx}")
            self.log(f"DEBUG: solutions for this slice are {self.solutions[idx]}")
            self.log(f"DEBUG: applied values for this slice are {self.solutionValuesToApply[idx]}")
            
            self.log(f"DEBUG: now waiting to actually perform the slice")
            if self.countScansCompleted(2):
                # perform the rest of these functions in another thread so that the shim setting doesn't lag behind too much
                def updateVals():
                    self.computeShimmedB0Map(idx)
                    self.evaluateShimImages()
                    trigger.finished.emit()
                kickoff_thread(updateVals)
            else:
                self.log("Error: Scans didn't complete")
                self.exsiInstance.images_ready_event.clear()
                self.exsiInstance.ready_event.clear()
    @requireExsiConnection
    @requireShimConnection
    @requireAssetCalibration
    def doAllShimmedScans(self):
        if not self.currentsComputedMarker.isChecked():
            return

        # compute how many scans needed, i.e. how many slices are not Nans out of the ROI
        startindex = None
        numindexes = 0
        for i in range(self.backgroundB0Map.shape[1]):
            if startindex is None and self.solutions[i] is not None:
                startindex = i
            if self.solutions[i] is not None:
                numindexes += 1
        startindex += 1 # chop off the first and last index
        numindexes -= 2 # chop off the first and last index
        self.log(f"DEBUG: ________________________Do All Shim Scans____________________________________")
        self.log(f"DEBUG: Starting at index {startindex} and doing {numindexes} B0MAPS")

        trigger = Trigger()
        trigger.finished.connect(self.updateShimImageAndStats)
        kickoff_thread(self.waitdoAllShimmedScans, args=(trigger,startindex, numindexes))
        
        def queueAll():
            for i in range(startindex, startindex + numindexes):
                self.shimSetAllCurrents(i) # set all of the currents
                self.queueBasisPairScan(preset=True)
        kickoff_thread(queueAll)

    def save(self):
        pass

    # ----------- random methods ------------ #

    def log(self, message):
        """Log a message to the GUI log and the shim log."""
        header = "SHIM TOOL:"
        log(header + message, self.debugging)
    

def handle_exit(signal_received, frame):
    # Handle any cleanup here
    print('SIGINT or CTRL-C detected. Trying to exit gracefully.')
    QApplication.quit()

if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)
    
    # try:
    config = load_config('config.json')
    tool = shimTool(config)
    tool.run()
