import numpy as np
from cvxopt import solvers, matrix
from dicomUtils import *

def compute_b0map(first, second, te1, te2):
    # Naively compute the b0 map using two phase images from the scans with different TEs
    return np.angle(np.conj(first)*second) / (2*np.pi) / ((te2-te1)*1e-3)

def compute_b0maps(n, localExamRootDir, threshFactor=.4):
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

def subtractBackground(background, b0maps):
    # NOTE: Assumes b0maps[0] is background and the rest are loops @ 1 A!!!!
    bases = []
    for i in range(len(b0maps)):
        bases.append(b0maps[i] - background)
    return bases

# TODO(rob): consider the offset in the position to make an affine linear gradient.
def addNaiveLinGrad(bases):
    # also create the linear gradients as values here:
    N, M, P = bases[0].shape
    
    # Create mesh grids for x, y, z axes
    x = np.linspace(-1, 1, N)
    y = np.linspace(-1, 1, M)
    z = np.linspace(-1, 1, P)
    basismult = np.meshgrid(x, y, z, indexing='ij')
    
    # Assuming you want the maximum Bz value at the edges of the volume to be a specific value
    # Adjust these max_values according to your requirements
    # TODO(rob): check this over....
    max_value = 4258*.3*9.6
    
    # Generate the spatially varying Bz field
    for i in range(3):
        bases.append(basismult[i]*max_value)

def createMask(background, bases, roi, sliceIndex=-1, orientation=Orientation.CORONAL):
    # require that one of background, bases and roi is not None
    if background is None and bases is None and roi is None:
        raise ValueError("At least one of background, bases or roi must be provided")

    masks = []
    if background is not None:
        masks.append(~np.isnan(background))
    if bases is not None:
        print(f"debug: {bases}")
        for base in bases:
            masks.append(~np.isnan(base))
    # then add roi if there is one
    if roi is not None:
        masks.append(~np.isnan(roi))
    # consider slice only if it is provided TODO(rob): add other orientations more nicely
    if sliceIndex >= 0: #and orientation == Orientation.CORONAL:
        if background is not None:
            sliceMask = np.zeros(background.shape)
        elif bases is not None:
            sliceMask = np.zeros(bases[0].shape)
        else:
            sliceMask = np.zeros(roi.shape)
        sliceMask[:,sliceIndex,:] = np.nan
        masks.append(np.isnan(sliceMask))

    # union the masks
    mask = masks[0]
    for m in masks[1:]:
        mask = mask & m
    
    return mask

def solveCurrents(background, rawBases, mask, withLinGrad=False, debug=False):
    # make a copy so that we can work with that instead
    bases = []

    for base in rawBases:
        bases.append(base.copy())

    # Add in the linear gradients if that is desired
    if withLinGrad:
        addNaiveLinGrad(bases)

    # vectorize using the final mask
    vectorized = []
    for i in range(len(bases)):
        masked = bases[i][mask] 
        if debug:
            print(f"DEBUG: vector {i} has nans : {np.isnan(masked).any()}")
        vectorized.append(masked)
    
    # Craft the Least Squar Problem
    A = np.stack(vectorized, axis=1)
    y = background[mask]

    if y.size == 0 or A.size == 0:
        return None

    if debug:
        # print and check if A or y still have nans
        print(f"DEBUG: A has nans: {np.isnan(A).any()}, y has nans: {np.isnan(y).any()}")
        print(f"DEBUG: A.shape {A.shape}, A.T.shape {A.T.shape}, y.shape: {y.shape}")
    p = 2 * A.T @ A
    q = 2 * y.T @ A

    # forming the constraint vectors depends on if there are lin gradient components
    if withLinGrad:
        g = np.vstack((np.eye(len(rawBases)+3), -np.eye(len(rawBases)+3)))
        h = 2 * np.ones(len(rawBases))
        h = np.concatenate((h, np.ones(3)*3.2934))
        h = np.concatenate((h, h))
    else:
        g = np.vstack((np.eye(len(rawBases)), -np.eye(len(rawBases))))
        h = 2 * np.ones(len(rawBases))
        h = np.concatenate((h, h))
    
    if debug:
        print(f"A.shape {A.shape}, y.shape: {y.shape}")
        print(f"p.shape {p.shape}, q.shape: {y.shape}, g.shape {g.shape}, h.shape: {h.shape}")
        print(p)
        print(g)
        print(h)
    
    try:
        res = solvers.qp(matrix(p), matrix(q), matrix(g), matrix(h))
    except ValueError as e:
        print(f"DEBUG: Error in solving the problem: {e}")
        return None

    return res['x']

def evaluate(d, debug=False):
    """ Evaluate a vector with basic stats"""
    std_og = np.nanstd(d)
    mean_og = np.nanmean(d)
    median_og = np.nanmedian(d)
    
    stats = f" RESULTS (Hz):\nSt. Dev: {std_og:.3f}\nMean: {mean_og:.3f}\nMedian: {median_og:.3f}"
    if debug:
        print(stats)
    return stats, std_og, mean_og, median_og
