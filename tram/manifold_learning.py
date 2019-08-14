#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Manifold learning methods
"""

# numerics imports
import numpy as np
import scipy
import scipy.sparse.linalg as sla
from sklearn.neighbors.kde import KernelDensity
from scipy.integrate import dblquad


#TODO rename epsi to gamma
def diffusionMaps(distMat, n_components=10, epsi=1., alpha=0.5):
    """
    Solve diffusion map eigenproblem
    
    Parameters
    ----------
    distMat: symmetric array of shape (n_features, n_features)

    n_components: int, number of eigenvalues and 
        eigenvectors to compute
        NOTE: n_components must be set at least
        to the number of desired dimensions in the
        diffusion space to which the data is projected
        by calling evaluateDiffusionMaps() later on
   
    Returns
    -------
    tuple of (eigenvalues, eigenvectors)  
        of the diffusion maps eigenproblem
    """
    
    npoints = distMat.shape[0]        
    kernelMat = np.exp(-distMat**2/epsi)
    rowsum  = np.sum(kernelMat,axis=0)
    
    # compensating for testpoint density
    kernelMat = kernelMat / np.outer(rowsum**alpha,rowsum**alpha)
    
    # row normalization
    kernelMat = kernelMat / np.tile(sum(kernelMat,0),(npoints,1))
    
    # weight matrix
    weightMat = np.diag(sum(kernelMat,0))
    
    #TODO optimize eigenvalue calculation -> eigsh
    #TODO rename eigs
    # solve the diffusion maps eigenproblem
    eigs = sla.eigs(kernelMat, n_components, weightMat)
    return eigs

def evaluateDiffusionMaps(eigs, n_components):
    """
    Transform data to diffusion map space
    
    Parameters
    ----------
    eigs: tuple of (eigenvalues, eigenvectors)  
        of the diffusion maps eigenproblem as returned
        by diffusionMaps(), where
        eigenvalues is an array of shape (n_features,)
        and eigenvectors is an array of shape
        (n_features, n_eigenvectors)
    n_components: int, number of dimensions in diffusion map space
        retained by dimensionality reduction
        NOTE: n_components must be smaller or equal to n_eigenvectors
   
    Returns
    -------
    array of shape (n_features, n_components)
        the transformed data in diffusion space  
    """
    
    #TODO rename eigs
    return eigs[1].real[:, :n_components] * eigs[0].real[np.newaxis, :n_components] 


def L2distance(system, cloud1, cloud2, rho, epsi):
    # 1/rho-weighted L2 distance between densities represented by point clouds
    
    # Compute Kernel density estimate from point clouds
    KDE1 = KernelDensity(kernel="gaussian", bandwidth=epsi).fit(cloud1)
    KDE2 = KernelDensity(kernel="gaussian", bandwidth=epsi).fit(cloud2)
    
    kde1fun = lambda x, y: np.exp(KDE1.score_samples(np.array([[x,y]])))
    kde2fun = lambda x, y: np.exp(KDE2.score_samples(np.array([[x,y]])))
    
    integrand = lambda x, y: (kde1fun(x,y) - kde2fun(x,y))**2 / rho(x,y)

    dist = dblquad(integrand, system.domain[0,0], system.domain[1,0], system.domain[0,1], system.domain[1,1])
    
    return dist