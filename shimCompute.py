import numpy as np
from cvxopt import solvers, matrix
from dicomUtils import *

def compute_b0map(first, second, te1, te2):
    # Naively compute the b0 map using two phase images from the scans with different TEs
    return np.angle(np.conj(first)*second) / (2*np.pi) / ((te2-te1)*1e-3)

def compute_b0maps(n, localExamRootDir):
    # NOTE: Assumes that n most recent scans are all basis pair scans.
    """ Computes the last n b0maps from pairs"""
    seriesPaths = listSubDirs(localExamRootDir)[-n*2:]
    b0maps = []
    for i in range(0, n, 2):
        phase1, te1, name1 = extractComplexImageData(seriesPaths[i])
        phase2, te2, name2 = extractComplexImageData(seriesPaths[i+1])
        b0map = compute_b0map(phase1, phase2, te1, te2)
        b0maps.append(b0map)
    return b0maps

def subtractBackground(b0maps):
    # NOTE: Assumes b0maps[0] is background and the rest are loops @ 1 A!!!!
    bases = []
    for i in range(len(b0maps)-1):
        bases.append(b0maps[i] - b0maps[0])
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

def creatMask(background, bases, roi=None, sliceIndex=-1, orientation=Orientation.CORONAL):
    # need to vectorize the inputs
    # to do so, we need to use a mask, first consider NaN vals after thresh mask
    masks = [~np.isnan(background)]
    for base in bases:
        masks.append(~np.isnan(base))
    # then add roi if there is one
    if roi is not None:
        masks.append(~np.isnan(roi))
    # consider slice only if it is provided TODO(rob): add other orientations more nicely
    if sliceIndex >= 0 and orientation == Orientation.CORONAL:
        sliceMask = np.zeros(background.shape)
        sliceMask[sliceIndex] = np.nan
        masks.append(np.isnan(sliceMask))

    # union the masks
    mask = masks[0]
    for m in masks[1:]:
        mask = mask & m
    
    return mask

def solveCurrents(background, rawBases, mask, withLinGrad=True, debug=False):
    # make a copy so that we can work with that instead
    bases = []
    for base in rawBases:
        bases.append(base.copy())

    # Add in the linear gradients if that is desired
    if withLinGrad:
        addNaiveLinGrad(bases)

    # vectorize using the final mask
    vectorized = []
    for base in bases:
        vectorized.append(base[mask])
    
    # Craft the Least Squar Problem
    A = np.stack(vectorized, axis=1)
    y = background[mask]

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
    
    res = solvers.qp(matrix(p), matrix(q), matrix(g), matrix(h))

    currents = res['x']

    return currents

def evaluate(d, debug=False):
    """ Evaluate a vector with basic stats"""
    std_og = np.nanstd(d)
    mean_og = np.nanmean(d)
    median_og = np.nanmedian(d)
    
    if debug:
        print(f" BASELINE RESULTS (Hz): \n\nSt. Dev: {std_og}\nMean: {mean_og}\nMedian: {median_og}\n\n")
    return std_og, mean_og, median_og
