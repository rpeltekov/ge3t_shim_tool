"""
File for all the Utility functions for the GUI
"""

import inspect
import time
from functools import partial

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from PyQt6.QtCore import QObject, QRectF, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QIntValidator, QLinearGradient, QPainter, QPen, QValidator
from PyQt6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
)


class ColorBarItem(QGraphicsItem):
    def __init__(self, min_val, max_val, parent=None):
        super(ColorBarItem, self).__init__(parent)
        self.min_val = min_val
        self.max_val = max_val
        self.num_ticks = 6  # Number of tick marks on the color bar
        self.tick_items = []  # To store tick mark items
        self.label_items = []  # To store label items

    def boundingRect(self):
        return QRectF(0, 0, 50, 475)  # Width and height of the color bar

    def paint(self, painter, option, widget):
        rect = self.boundingRect()
        gradient_rect = QRectF(10, 0, 40, 475)  # Adjust the gradient rectangle to be slightly smaller

        # Define the gradient
        gradient = QLinearGradient(
            gradient_rect.left(), gradient_rect.top(), gradient_rect.left(), gradient_rect.bottom()
        )
        gradient.setColorAt(1, QColor(0, 0, 0))  # black
        gradient.setColorAt(0, QColor(255, 255, 255))  # white

        painter.fillRect(gradient_rect, gradient)

        # Calculate positions for numerical labels
        tick_positions = [rect.top() + i * (rect.height() / (self.num_ticks - 1)) for i in range(self.num_ticks)]

        # Draw tick marks and labels
        font = QFont()
        font.setPixelSize(8)  # Adjust font size as needed
        painter.setFont(font)

        pen = QPen(QColor(0, 0, 0))  # Pen for tick marks
        painter.setPen(pen)

        # Clear previous tick and label items
        for item in self.tick_items + self.label_items:
            if item.scene():
                item.scene().removeItem(item)
        self.tick_items.clear()
        self.label_items.clear()

        for pos in tick_positions:

            tick_line = QGraphicsLineItem(
                round(gradient_rect.left()) - 10, round(pos), round(gradient_rect.left()) - 5, round(pos), self
            )
            self.tick_items.append(tick_line)
            # self.scene().addItem(tick_line)  # Add tick mark to the scene

            value = self.max_val - ((pos - rect.top()) / rect.height()) * (self.max_val - self.min_val)
            tick_text = QGraphicsTextItem(f"{value:.2f}", self)
            tick_text.setFont(font)
            tick_text.setDefaultTextColor(QColor(0, 0, 0))
            tick_text.setPos(round(gradient_rect.left()) - 45, round(pos) - 8)
            self.label_items.append(tick_text)
            # self.scene().addItem(tick_text)  # Add label to the scene


class ColorBar(QGraphicsView):
    def __init__(self, parent=None):
        super(ColorBar, self).__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.colorbar_item = None
        self.update_colorbar(np.array([]))

    def update_colorbar(self, data):
        self.scene.clear()
        if data.size == 0:
            return
        min_val = np.nanmin(data)
        max_val = np.nanmax(data)
        self.colorbar_item = ColorBarItem(min_val, max_val)
        self.scene.addItem(self.colorbar_item)

        # fig, ax = plt.subplots(figsize=(1, 5))
        # norm = plt.Normalize(vmin=np.nanmin(data), vmax=np.nanmax(data))
        # fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap='gray'), cax=ax)

        # canvas = FigureCanvas(fig)
        # canvas.draw()
        # pixmap = canvas.grab()
        # self.colorbar_item = self.scene.addPixmap(pixmap)


class LogMonitorThread(QThread):
    update_log = pyqtSignal(str)

    def __init__(self, filename, parent=None):
        super(LogMonitorThread, self).__init__(parent)
        self.filename = filename
        self.running = True

    def run(self):
        self.running = True
        with open(self.filename, "r") as file:
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
    def __init__(self, parent=None, layout=None):
        super(ImageViewer, self).__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.pixmap_item = None
        self.qImage: QImage = None
        self.viewData = None  # 2D data that the image viewer is currently being set to show
        self.label = QLabel()
        self.width = 512
        self.height = 512
        self.setFixedSize(self.width, self.height)  # Set a fixed size for the view
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.colorbar = ColorBar()
        self.colorbar.setFixedWidth(110)  # Set a fixed width for the color bar
        self.colorbar.setFixedHeight(self.height)  # Match height with the viewer

        # Layout to include image and color bar
        # Not sure if I should put the part below in Gui.py or here, like most of the add widget functions have been in gui.py

        hlayout = QHBoxLayout()
        layout.addLayout(hlayout)
        hlayout.addWidget(self, alignment=Qt.AlignmentFlag.AlignCenter)
        hlayout.addWidget(self.colorbar)
        layout.addWidget(self.label)

        # def update_colorbar(viewer, data):
        #     viewer.viewData = data
        #     viewer.colorbar.update_colorbar(data)

        # TODO issue #7 add color bar
        # np.nanmax and nanmin, set black to minimum and white to maximum

    def set_pixmap(self, pixmap):
        if self.pixmap_item is None:
            self.pixmap_item = self.scene.addPixmap(pixmap)
        else:
            self.pixmap_item.setPixmap(pixmap)
        self.pixmap = pixmap.toImage()

        if pixmap is not None and self.colorbar is not None:
            self.colorbar.update_colorbar(self.viewData)

    def mouseMoveEvent(self, event):
        if self.pixmap_item is not None and self.label is not None and self.viewData is not None:
            point = self.mapToScene(event.pos())
            x, y = int(point.x()), int(point.y())
            if 0 <= x < self.pixmap.width() and 0 <= y < self.pixmap.height():
                color = self.pixmap.pixelColor(x, y)
                hz = self.viewData[y, x]
                # Assuming there's a method to update a status bar or label:
                self.label.setText(f"Coordinates: ({x}, {y}) - Value: {hz:.4f}")
            else:
                self.label.clear()


def createMessageBox(title, text, informativeText):
    msg = QMessageBox()
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setWindowTitle(title)
    msg.setText(text)
    msg.setInformativeText(informativeText)
    msg.setStandardButtons(QMessageBox.StandardButton.Ok)
    msg.exec()


class Trigger(QObject):
    """Class to trigger a signal when a function is finished."""

    finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.success = False


# ------------ gui objects ------------


def addButtonConnectedToFunction(layout: QBoxLayout, buttonName: str, function):
    button = QPushButton(buttonName)
    button.clicked.connect(function)
    layout.addWidget(button)
    return button


def addEntryWithLabel(layout: QBoxLayout, labelStr: str, entryvalidator: QValidator):
    label = QLabel(labelStr)
    entry = QLineEdit()
    entry.setValidator(entryvalidator)
    labelEntryLayout = QHBoxLayout()
    labelEntryLayout.addWidget(label)
    labelEntryLayout.addWidget(entry)
    layout.addLayout(labelEntryLayout)
    return entry


def addLabeledSlider(
    layout: QBoxLayout,
    labelStr: str,
    granularity: int,
    orientation=Qt.Orientation.Horizontal,
):
    slider = QSlider(orientation)
    label = QLabel(labelStr)
    labelEntryLayout = QHBoxLayout()
    labelEntryLayout.addWidget(label)
    labelEntryLayout.addWidget(slider)
    slider.setMinimum(1)
    slider.setMaximum(granularity)
    slider.setValue((round(granularity) // 2))
    layout.addLayout(labelEntryLayout)
    return slider


def addLabeledSliderAndEntry(layout: QBoxLayout, labelStr: str, updateFunc):
    """
    Add a slider and entry to the layout with the given label
    default with value 0
    On updates, they will update each other and call the updateFunc provided
    """
    label = QLabel(labelStr)

    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setMinimum(0)
    slider.setMaximum(0)
    slider.setValue(0)

    entry = QLineEdit()
    entry.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
    entry.setValidator(QIntValidator(0, 0))
    entry.setText(str(0))

    slider.valueChanged.connect(partial(updateSlider, entry, updateFunc))
    entry.editingFinished.connect(partial(updateEntry, entry, slider, updateFunc))

    labelEntryLayout = QHBoxLayout()
    labelEntryLayout.addWidget(label)
    labelEntryLayout.addWidget(slider)
    labelEntryLayout.addWidget(entry)
    layout.addLayout(labelEntryLayout)
    return slider, entry


def updateSliderEntryLimits(slider: QSlider, entry: QLineEdit, minVal: int, maxVal: int, defaultVal: int = None):
    slider.setMinimum(minVal)
    slider.setMaximum(maxVal)
    entry.setValidator(QIntValidator(minVal, maxVal))
    if defaultVal is not None:
        slider.setValue(defaultVal)
        entry.setText(str(defaultVal))
    else:
        entry.setText(str(slider.value()))


def addButtonWithFuncAndMarker(layout: QBoxLayout, buttonName: str, function, markerName="Done?"):
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


# ------------ gui update helper functions ------------ #


def updateEntry(entry: QLineEdit, slider: QSlider, updateFunc):
    """
    Update the slider to match the entry, and call the updateFunc with the new value
    """
    index = int(entry.text()) if entry.text() else 0
    slider.setValue(index)
    if updateFunc:
        if len(inspect.signature(updateFunc).parameters) > 0:
            updateFunc(index)
        else:
            updateFunc()


def updateSlider(entry: QLineEdit, updateFunc, value: int):
    """Update the entry to match the slider, and call the updateFunc with the new value"""
    entry.setText(str(value))
    if updateFunc:
        if len(inspect.signature(updateFunc).parameters) > 0:
            updateFunc(value)
        else:
            updateFunc()


# ------------ gui ROI shapes ------------ #
class ROIObject:
    """
    General ROI object to hold the parameters of an ROI
    """

    def __init__(self) -> None:
        self.sizes = [0, 0, 0]
        self.centers = [0, 0, 0]
        self.sliderSizes = [0, 0, 0]
        self.sliderCenters = [0, 0, 0]

        self.xdim = 0
        self.ydim = 0
        self.zdim = 0

        self.updated = False
        self.enabled = False
        self.mask = None

    def setROILimits(self, xdim, ydim, zdim):
        self.xdim = xdim
        self.ydim = ydim
        self.zdim = zdim

    def getSlicePoints(self, sliceIdx: int):
        """
        Return the points of the ellipse on the given slice
        """
        points = []
        if self.enabled:
            for x in range(self.xdim):
                for y in range(self.ydim):
                    if self.isIMGPointInROI(x, y, sliceIdx):
                        points.append((x, y))
        return points

    def getROIMask(self):
        """
        Return the 3D numpy boolean mask of the ROI
        """
        if not self.enabled:
            return None
        if self.updated:
            # TODO issue #1
            mask = np.zeros((self.ydim, self.zdim, self.xdim), dtype=bool)
            for z in range(self.zdim):
                for x in range(self.xdim):
                    for y in range(self.ydim):
                        mask[y, z, x] = self.isIMGPointInROI(x, y, z)
                        self.mask = mask
        return self.mask

    def isIMGPointInROI(self, x, y, z):
        """
        Check if the point is in the ROI
        """
        raise NotImplementedError


class ellipsoidROI(ROIObject):
    """
    Class to hold the parameters of an ellipsoid ROI
    """

    def __init__(self) -> None:
        super().__init__()

    def isIMGPointInROI(self, x, y, z):
        return (
            (x - self.centers[0]) ** 2 / self.sizes[0] ** 2
            + (y - self.centers[1]) ** 2 / self.sizes[1] ** 2
            + (z - self.centers[2]) ** 2 / self.sizes[2] ** 2
        ) <= 1
