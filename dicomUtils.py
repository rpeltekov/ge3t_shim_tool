import os
import numpy as np
import pydicom
from enum import Enum

class Orientation(Enum):
    CORONAL = 1
    SAGITTAL = 2
    AXIAL = 3

def listSubDirs(directory):
    # Function to list all DICOM files in a directory
    if os.path.exists(directory):
        subdirs = [os.path.join(directory, d) for d in os.listdir(directory) if os.path.isdir(os.path.join(directory, d))]
        subdirs.sort(key=lambda f: int(f.split('/')[-1][1:]))
        return subdirs
    else:
        print(f"Directory {directory} does not exist.")
        return []

def listDicomFiles(dcmSeriesDir):
    # Function to list all DICOM files in a dcmSeriesDir
    if len(os.listdir(dcmSeriesDir)) > 0:
        dicom_files = [os.path.join(dcmSeriesDir, f) for f in os.listdir(dcmSeriesDir)]
        dicom_files.sort(key=lambda f: int(f.split('.')[-1]))
        return dicom_files

def get_orientation(orientation_cosines):
    x, y = orientation_cosines[:3], orientation_cosines[3:6]
    x = np.array(x)
    y = np.array(y)

    # Typical vector components for axial, sagittal, coronal can be predefined:
    axial = np.array([1, 0, 0]), np.array([0, 1, 0])
    sagittal = np.array([0, 1, 0]), np.array([0, 0, 1])
    coronal = np.array([1, 0, 0]), np.array([0, 0, 1])

    # Check for major axis alignment by comparing dot products:
    if np.isclose(np.dot(x, axial[0]), 1) and np.isclose(np.dot(y, axial[1]), 1):
        return Orientation.AXIAL
    elif np.isclose(np.dot(x, sagittal[0]), 1) and np.isclose(np.dot(y, sagittal[1]), 1):
        return Orientation.SAGITTAL
    elif np.isclose(np.dot(x, coronal[0]), 1) and np.isclose(np.dot(y, coronal[1]), 1):
        return Orientation.CORONAL
    else:
        return None  # Orientation not standard or mixed

def extractMetadata(dcm):
    try:
        te = getattr(dcm, 'EchoTime')
        # TODO(rob): implement the series descriptor, so you can append to the top of the visualizer
        series_desc = getattr(dcm, 'SeriesDescription')

        # add orientation
        # if 'Image Orientation (Patient)' in dcm:
        #     orientation_cosines = dcm['Image Orientation (Patient)'].value
        #     orientation = get_orientation(orientation_cosines)
        # else:
        orientation = None 

        return te, series_desc
    except Exception as e:
        print(f"Error extracting metadata: {e}")
        return None, None

def extractComplexImageData(dcmSeriesPath, threshFactor=.5):
    # NOTE: Assumes that Mag, I, Q images are interleaved in the series!!!
    # Process a dicom directory and pulls out masked complex data
    paths = listDicomFiles(dcmSeriesPath)
    if paths is None:
        raise Exception("No Scans Exist Yet In the Local Directory...")
    mags = []
    Is = []
    Qs = []
    te = None
    for i in range(0, len(paths), 3):
        mag = pydicom.dcmread(paths[i])
        I = pydicom.dcmread(paths[i+1])
        Q = pydicom.dcmread(paths[i+2])
        mags.append(mag.pixel_array)
        Is.append(I.pixel_array)
        Qs.append(Q.pixel_array)
        if te is None:
            te, name = extractMetadata(mag)
    mags = np.stack(mags, axis=0)
    Is = np.stack(Is, axis=0)
    Qs = np.stack(Qs, axis=0)
    phase = Is + 1j*Qs

    thresh = np.mean(mags) * threshFactor
    mask = mags < thresh
    phase[mask] = np.nan

    return phase, te, name

def extractBasicImageData(dcmSeriesPath, stride=1, offset=0):
    # Process a dicom directory and pull out the image data from it along with te
    paths = listDicomFiles(dcmSeriesPath)
    if paths is None:
        raise Exception("No Scans Exist Yet In the Local Directory...")
    data3d = []
    te = None
    for i in range(0, len(paths), stride):
        data = pydicom.dcmread(paths[i+offset])
        data3d.append(data.pixel_array)
        te, orientation = extractMetadata(data)
    data3d = np.stack(data3d, axis=0)
    return data3d, te, orientation

