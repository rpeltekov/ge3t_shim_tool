import numpy as np
from cvxopt import solvers, matrix
from dicomUtils import *
from typing import List
from utils import *
from skimage.restoration import unwrap_phase

def compute_b0map(first, second, te1, te2):
    # Naively compute the b0 map using two phase images from the scans with different TEs
    angle = np.angle(np.conj(first)*second)
    nan_mask = np.isnan(angle)
    angle_filled = np.nan_to_num(angle, nan=0.0)
    angle = np.ma.array(angle_filled, mask=nan_mask)
    angle = unwrap_phase(angle)   
    angle = np.ma.filled(angle, fill_value=np.nan)
    return  angle / (2*np.pi) / ((te2-te1)*1e-3)

def compute_b0maps(n, localExamRootDir, threshFactor=.4) -> List[np.ndarray]:
    # NOTE: Assumes that n most recent scans are all basis pair scans.
    """ Computes the last n b0maps from pairs"""
    seriesPaths = listSubDirs(localExamRootDir)
    seriesPaths = seriesPaths[-n*2:]
    b0maps = []
    for i in range(0, n*2, 2):
        phase1, te1, name1 = extractComplexImageData(seriesPaths[i], threshFactor=threshFactor)
        print(f"DEBUG: Extracted te1 {te1}, name1 {name1}")
        phase2, te2, name2 = extractComplexImageData(seriesPaths[i+1], threshFactor=threshFactor)
        print(f"DEBUG: Extracted te2 {te2}, name2 {name2}")
        b0map = compute_b0map(phase1, phase2, te1, te2)
        b0maps.append(b0map)
    return b0maps

def subtractBackground(background, b0maps) -> List[np.ndarray]:
    # NOTE: Assumes b0maps[0] is background and the rest are loops @ 1 A!!!!
    bases = []
    for i in range(len(b0maps)):
        bases.append(b0maps[i] - background)
    return bases

def maskOneSlice(mask, sliceIdx) -> np.ndarray:
    """Return the mask with only one slice filled; 3D Array in CORONAL ORIENTATION"""
    newMask = np.zeros_like(mask)
    newMask[:,sliceIdx,:] = mask[:,sliceIdx,:]
    return newMask

def createMask(background: np.ndarray, bases: List[np.ndarray], roi: np.ndarray) -> np.ndarray:
    """Create 3d boolean mask from background, bases and ROI"""
    # require that one of background, bases and roi is not None
    if background is None and np.array([base is None for base in bases]).any() and roi is None:
        raise ShimComputeError("At least one of background, bases or roi must be provided")
    
    masks = []
    if background is not None:
        masks.append(~np.isnan(background))

    if np.array([base is not None for base in bases]).all():
        for base in bases:
            masks.append(~np.isnan(base))

    # then add roi if there is one; should already be boolean mask
    if roi is not None:
        masks.append(roi)

    # union the masks
    mask = masks[0]
    for m in masks[1:]:
        mask = mask & m
    
    return mask

def solveCurrents(background, rawBases, mask, gradientCalStrength, loopCalStrength, debug=False, gradientMax_ticks=100, loopMaxCurrent_mA=2000) -> np.ndarray:
    # make a copy so that we can work with that instead
    bases = []

    bases.append(np.ones(background.shape)) # add the constant basis for center frequency calc
    for base in rawBases:
        bases.append(base.copy())

    vectorized = []
    for i in range(len(bases)):
        masked = bases[i][mask] 
        # if debug:
            # print(f"DEBUG: vector {i} has nans : {np.isnan(masked).any()}")
        vectorized.append(masked)
    
    # Craft the constrained Least Squares Problem
    A = np.stack(vectorized, axis=1)
    y = background[mask]

    if y.size == 0 or A.size == 0:
        return None

    # if debug:
        # print and check if A or y still have nans
        # print(f"DEBUG: A has nans: {np.isnan(A).any()}, y has nans: {np.isnan(y).any()}")
        # print(f"DEBUG: A.shape {A.shape}, A.T.shape {A.T.shape}, y.shape: {y.shape}")
        # print(f"DEBUG: y.mean: {np.mean(y)}, y.std: {np.std(y)} y.min: {np.min(y)}, y.max: {np.max(y)}")
    p = 2 * A.T @ A
    q = 2 * y.T @ A

    # constraint vectors
    g = np.vstack((np.eye(len(rawBases)+1), -np.eye(len(rawBases)+1))) # plus 4 for cf and 3 lin grads
    h = np.ones(1)*2000 # for the center frequency in hz
    h = np.concatenate((h, gradientMax_ticks / (np.ones(3)*gradientCalStrength))) # for the linear gradients 
    h = np.concatenate((h, loopMaxCurrent_mA / (np.ones(len(rawBases)-3)*loopCalStrength)))
    h = np.concatenate((h, h)) # double it for the negative constraints
    
    # TODO(rob): fix these debug comments to show less
    # if debug:
        # print(f"A.shape {A.shape}, y.shape: {y.shape}")
        # print(f"p.shape {p.shape}, q.shape: {y.shape}, g.shape {g.shape}, h.shape: {h.shape}")
        # print(p)
        # print(g)
        # print(h)
    
    try:
        solvers.options['show_progress'] = False
        res = solvers.qp(matrix(p), matrix(q), matrix(g), matrix(h))
    except ValueError as e:
        print(f"DEBUG: Error in solving the problem; Likely due to singular matrix bc trying to solve for something outside ROI")
        return None

    return np.array(res['x']).flatten()

def evaluate(d, debug=False):
    """ Evaluate a vector with basic stats"""
    std_og = np.nanstd(d)
    mean_og = np.nanmean(d)
    median_og = np.nanmedian(d)
    rmse = np.sqrt(np.nanmean(d**2))
    
    stats = f" RESULTS (Hz):\nSt. Dev: {std_og:.3f}\nMean: {mean_og:.3f}\nMedian: {median_og:.3f}\nRMSE: {rmse:.3f}"
    # if debug:
    #     print(stats)
    return stats, [std_og, mean_og, median_og, rmse]

class ShimComputeError(Exception):
    pass