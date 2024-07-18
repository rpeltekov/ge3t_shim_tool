import json
import sys

import numpy as np
import pydicom

sys.path.append("..")
from shimTool.dicomUtils import *
from shimTool.shimCompute import *
from shimTool.Tool import Tool
from shimTool.utils import *


def extractPixelSize(firstDCM):
    dcm = pydicom.dcmread(firstDCM)
    # Extract X and Y pixel size from PixelSpacing
    if "PixelSpacing" in dcm:
        pixel_spacing = dcm.PixelSpacing
        pixel_size_x, pixel_size_y = pixel_spacing
    else:
        raise ValueError("Pixel Spacing information is not available in this DICOM file.")

    # Extract Z pixel size from SpacingBetweenSlices or SliceThickness
    if "SpacingBetweenSlices" in dcm:
        pixel_size_z = dcm.SpacingBetweenSlices
    elif "SliceThickness" in dcm:
        pixel_size_z = dcm.SliceThickness
    else:
        raise ValueError("Z spacing information is not available in this DICOM file.")

    return [pixel_size_x, pixel_size_y, pixel_size_z]


def computeFieldmapFromLatestFieldmapScan(tool: Tool) -> np.ndarray:
    b0maps = compute_b0maps(1, tool.localExamRootDir)
    return b0maps[0]


def computeFieldmapFromFirstSeriesName(n, localExamRootDir, threshFactor=0.4) -> List[np.ndarray]:
    # computes the fieldmap from n scans ago
    """Computes the last n b0maps from pairs"""
    n = n + 1
    seriesPaths = listSubDirs(localExamRootDir)
    print(f"DEBUG: Found {len(seriesPaths)} seriesPaths")
    if n == 1:
        seriesPaths = seriesPaths[-2 * n :]
    else:
        seriesPaths = seriesPaths[-n * 2 : -(n - 1) * 2]
    b0maps = []
    for i in range(0, 2, 2):
        data1 = extractComplexImageData(seriesPaths[i], threshFactor=threshFactor)
        phase1, te1, name1 = data1
        print(f"DEBUG: Extracted te1 {te1}, name1 {name1}")
        data2 = extractComplexImageData(seriesPaths[i + 1], threshFactor=threshFactor)
        phase2, te2, name2 = data2
        print(f"DEBUG: Extracted te2 {te2}, name2 {name2}")
        b0map = compute_b0map(phase1, phase2, te1, te2)
        b0maps.append(b0map)
    return b0maps[0]

def load_config(filename):
    with open(filename, "r") as file:
        return json.load(file)

def load_tool():
    if "tool" in globals():
        del globals()["tool"]
    config = load_config("/home/heartvista/Documents/robert/ge3t_shim_tool/config.json")
    return Tool(config, debugging=True)
