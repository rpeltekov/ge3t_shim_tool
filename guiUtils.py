"""
File for all the Utility functions for the GUI
"""

import time
from PyQt6.QtWidgets import QMessageBox, QPushButton, QLabel, QLineEdit, QHBoxLayout, QSlider, QSizePolicy, QCheckBox
from PyQt6.QtCore import pyqtSignal, QObject, QThread, pyqtSignal, Qt
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene

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

class ImageViewer(QGraphicsView):
    def __init__(self, parent=None, label=None):
        super(ImageViewer, self).__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.pixmap_item = None
        self.label = label

    def set_pixmap(self, pixmap):
        if self.pixmap_item is None:
            self.pixmap_item = self.scene.addPixmap(pixmap)
        else:
            self.pixmap_item.setPixmap(pixmap)
        self.pixmap = pixmap.toImage()

    def mouseMoveEvent(self, event):
        if self.pixmap_item is not None:
            point = self.mapToScene(event.pos())
            x, y = int(point.x()), int(point.y())
            if 0 <= x < self.pixmap.width() and 0 <= y < self.pixmap.height():
                color = self.pixmap.pixelColor(x, y)
                # Assuming there's a method to update a status bar or label:
                self.label.setText(f"Coordinates: ({x}, {y}) - Grayscale Value: {color.value()}")
            else:
                self.label.clear()

def createMessageBox(title, text, informativeText):
    msg = QMessageBox()
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setWindowTitle(title)
    msg.setText(text)
    msg.setInformativeText(informativeText)
    msg.setStandardButtons(QMessageBox.StandardButton.Ok)
    return msg

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
    """ Class to trigger a signal when a function is finished. """
    finished = pyqtSignal()

# ------------ gui objects ------------


def addButtonConnectedToFunction(layout, buttonName, function):
    button = QPushButton(buttonName)
    button.clicked.connect(function)
    layout.addWidget(button)
    return button

def addEntryWithLabel(layout, labelStr, entryvalidator):
    label = QLabel(labelStr)
    entry = QLineEdit()
    entry.setValidator(entryvalidator)
    labelEntryLayout = QHBoxLayout()
    labelEntryLayout.addWidget(label)
    labelEntryLayout.addWidget(entry)
    layout.addLayout(labelEntryLayout)
    return entry

def addLabeledSlider(layout, labelStr, granularity, orientation=Qt.Orientation.Horizontal):
    slider = QSlider(orientation)
    label = QLabel(labelStr)
    labelEntryLayout = QHBoxLayout()
    labelEntryLayout.addWidget(label)
    labelEntryLayout.addWidget(slider)
    slider.setMinimum(0)
    slider.setMaximum(granularity)
    slider.setValue((round(granularity)//2))
    layout.addLayout(labelEntryLayout)
    return slider

def addLabeledSliderAndEntry(layout, labelStr, entryvalidator):
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

def addButtonWithFuncAndMarker(layout, buttonName, function, markerName="Done?"):
    hlayout = QHBoxLayout()
    layout.addLayout(hlayout)
    marker = QCheckBox(markerName)
    marker.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    if markerName == "Done?":
        marker.setEnabled(False)
        button = addButtonConnectedToFunction(hlayout, buttonName, function)
        hlayout.addWidget(marker)
    else:
        hlayout.addWidget(marker)
        button = addButtonConnectedToFunction(hlayout, buttonName, function)
    return button, marker