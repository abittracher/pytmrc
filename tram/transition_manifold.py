#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Transition Manifold-related classes and methods
"""

# numerics imports
import numpy as np
from scipy.spatial import cKDTree
from scipy.sparse.linalg import eigsh
from scipy.ndimage.interpolation import shift
from sklearn.neighbors.kde import KernelDensity
from scipy.integrate import dblquad
from sklearn.kernel_approximation import RBFSampler, Nystroem


# utility imports
from tqdm import tqdm

# TM imports
import tram.manifold_learning as ml


class TransitionManifold:

    rc = None

    def __init__(self, system, xtest):
        self.system = system
        self.xtest = xtest


# TM based on RKHS-embeddings of parallel short simulations
class KernelBurstTransitionManifold(TransitionManifold):

    def __init__(self, kernel, epsi=1.):
        self.kernel = kernel
        self.epsi = epsi

    def fit(self, X, showprogress = True):
        npoints, self.M = X.shape[:2]
        X = _reshape(X)

        # compute symmetric kernel evaluations
        dXX = []
        print("Computing symmetric kernel evaluations...")
        for i in tqdm(range(npoints), disable = not showprogress):
            GXX = self.kernel.evaluate(X[i::npoints,:], X[i::npoints,:])
            dXX = np.append(dXX, np.sum(GXX))

        # compute asymmetric kernel evaluations and assemble distance matrix
        distMat = np.zeros((npoints, npoints))
        print("Computing asymmetric kernel evaluations...")
        for i in tqdm(range(npoints), disable = not showprogress):
            for j in range(i):
                GXY = self.kernel.evaluate(X[i::npoints,:], X[j::npoints,:])
                distMat[i,j] = (dXX[i] + dXX[j] - 2*np.sum(GXY)) / self.M**2
        distMat = distMat + np.transpose(distMat)

        # compute diffusion maps coordinates
        eigs = ml.diffusionMaps(distMat, epsi=self.epsi)
        self.rc = eigs
        self.distMat = distMat

    def predict(self, Y):
        #TODO
        pass




# TM based on RKHS-embeddings of a single long trajectory
class KernelTrajTransitionManifold(TransitionManifold):

    def __init__(self, system, kernel, xtest, traj, lag, epsi=1.):
        self.system = system
        self.kernel = kernel
        self.xtest = xtest
        self.traj = traj
        self.lag = lag
        self.epsi = epsi

    def computeRC(self, showprogress = True):
        npoints = np.size(self.xtest,0)

        # indices of test points closest to trajectory points
        print("Sorting into Voronoi cells...")
        kdTree = cKDTree(self.xtest)
        closest = kdTree.query(self.traj, n_jobs=-1)[1]

        # extract point clouds from trajectory
        pointclouds = []
        print("Assigning trajectory points to centers...")
        for i in tqdm(range(npoints)):
            laggedInd = shift(closest==i, self.lag, cval=False) # indices of lagged points
            pointclouds.append(self.traj[laggedInd,:])

        # compute symmetric kernel evaluations
        dXX = []
        print("Computing symmetric kernel evaluations...")
        for i in tqdm(range(npoints), disable = not showprogress):
            GXX = self.kernel.evaluate(pointclouds[i], pointclouds[i])
            dXX = np.append(dXX, np.sum(GXX))

        # compute asymmetric kernel evaluations and assemble distance matrix
        distMat = np.zeros((npoints, npoints))
        print("Computing asymmetric kernel evaluations...")
        for i in tqdm(range(npoints), disable = not showprogress):
            nTrajpointsi = np.size(pointclouds[i],0)
            for j in range(i):
                nTrajpointsj = np.size(pointclouds[j],0)
                GXY = self.kernel.evaluate(pointclouds[i], pointclouds[j])
                distMat[i,j] = dXX[i]/nTrajpointsi**2 + dXX[j]/nTrajpointsj**2 - 2*np.sum(GXY)/(nTrajpointsi*nTrajpointsj)
        distMat = distMat + np.transpose(distMat)

        eigs = ml.diffusionMaps(self.xtest, distMat, epsi=self.epsi)
        self.rc = eigs
        self.distMat = distMat



# TM based on random Whitney embeddings of parallel short simulations
class EmbeddingBurstTransitionManifold(TransitionManifold):

    def __init__(self, system, embfun, xtest, t, dt, M, epsi=1.):
        self.system = system
        self.embfun = embfun
        self.xtest = xtest
        self.t = t
        self.dt = dt
        self.M = M
        self.epsi = epsi

    def computeRC(self, showprogress=True):
        npoints = np.size(self.xtest,0)

        # compute the time evolution of all test points at once, for performance reasons
        x0 = np.tile(self.xtest, (self.M,1))
        pointclouds = self.system.computeBurst(self.t, self.dt, x0, showprogress=showprogress)

        # embedd each point cloud into R^k
        embpointclouds = np.zeros((0,(self.embfun).outputdimension))
        print("Evaluating observables...")
        for i in tqdm(range(npoints), disable = not showprogress):
            y = self.embfun.evaluate(pointclouds[i::npoints,:])
            embpointclouds = np.append(embpointclouds, [np.sum(y,0)/self.M], axis=0)
        self.embpointclouds = embpointclouds

        # compute diffusion maps coordinates on embedded points
        eigs = ml.diffusionMaps(embpointclouds, epsi=self.epsi)
        self.rc= eigs



# random linear embedding function for the Whitney embedding
class RandomLinearEmbeddingFunction():

    def __init__(self, inputdimension, outputdimension, seed):
        self.inputdimension = inputdimension
        self.outputdimension = outputdimension
        self.seed = seed

        # draw the random coefficients
        np.random.seed(self.seed)
        A = np.random.uniform(0, 1, (self.inputdimension,self.outputdimension))
        #self.A,_ = np.linalg.qr(A,mode='complete')
        self.A = A


    def evaluate(self, x):
        y = x.dot(self.A)
        return y



# TM based on direct L2-distance comparison between densities represented by parallel shor simulations
class L2BurstTransitionManifold(TransitionManifold):

    def __init__(self, rho, domain, epsi=1., kde_epsi=0.1):
        self.rho = rho
        self.epsi = epsi
        self.domain = domain
        self.kde_epsi = kde_epsi

    def L2distance(self, cloud1, cloud2):
        # 1/rho-weighted L2 distance between densities represented by point clouds

        KDE1 = KernelDensity(kernel="gaussian", bandwidth=self.kde_epsi).fit(cloud1)
        KDE2 = KernelDensity(kernel="gaussian", bandwidth=self.kde_epsi).fit(cloud2)

        kde1fun = lambda x, y: np.exp(KDE1.score_samples(np.array([[x,y]])))
        kde2fun = lambda x, y: np.exp(KDE2.score_samples(np.array([[x,y]])))

        integrand = lambda x, y: (kde1fun(x,y) - kde2fun(x,y))**2 / self.rho(x,y)

        dist = dblquad(integrand, self.domain[0,0], self.domain[1,0], self.domain[0,1], self.domain[1,1])
        return dist

    def fit(self, X, showprogress=True):
        npoints = np.size(X,0)
        X = _reshape(X)

        # compute distance matrix
        distMat = np.zeros((npoints, npoints))
        print("Computing distance matrix...")
        for i in tqdm(range(npoints), disable = not showprogress):
            for j in range(npoints):
                distMat[i,j] = self.L2distance(X[i::npoints,:], X[j::npoints,:])[0]
        self.distMat = distMat

        # compute diffusion maps coordinates on embedded points
        #eigs = ml.diffusionMaps(embpointclouds, epsi=self.epsi)
        #self.rc= eigs


class LinearRandomFeatureManifold(TransitionManifold):
    """
    A class providing a linear transition manifold
    by using kernel feature Approximations. The kernel embeddings
    of the transition densities are approximated with
    either random Fourier features or the Nystroem method.

    Please refer also to the documentation of sklearn.kernel_approximation
    """

    def __init__(self, method="rff", n_components=100, kernel="rbf", gamma=.1, **kwargs):
        """
        TODO document interface
        TODO add output dimension choice to PCA routine

        Parameters
        ----------
        method : str
            specifies the used feature approximation. Can either be 'rff' or 'nystroem'
        n_components : int
            number of dimensions in the feature approximation space.
        """
        self.method = method
        self.gamma = gamma
        self.kernel = kernel
        self.n_components = n_components
        self.kwargs = kwargs

        self.sampler = None
        self.embedded = None
        self.vec = None

    def fit(self, X):
        """
        Computes a linear reaction coordinate based on the data X.

        Parameters
        ----------
        X : np.array of shape [# startpoints, # simulations per startpoint, dimension]
            data array containing endpoints of trajectory simulations for each startpoint
        """

        self.n_points = X.shape[0] # number of start points
        self.M = X.shape[1] # number of simulations per startpoint
        self.dim = X.shape[2]

        if self.method == "rff":
            self.sampler = RBFSampler(gamma = self.gamma, n_components=self.n_components,
                                      **self.kwargs)
        elif self.method == "nystroem":
            self.sampler = Nystroem(kernel=self.kernel, n_components=self.n_components,
                                    **self.kwargs)
        else:
            raise ValueError("Instantiate with either method='rff' or sampler='nystroem'")

        #compute approximation space mean embeddings
        self.embedded = self.sampler.fit_transform(X.reshape(self.n_points * self.M, self.dim))
        self.embedded = self.embedded.reshape(self.n_points, self.M, self.n_components).sum(axis=1) #/ self.M

        #covariance matrix
        mean = self.embedded.sum(axis=0) / self.n_points
        self.embedded = self.embedded - mean
        cov = self.embedded.T @ self.embedded # n_components x n_components
        _, self.vec = eigsh(cov, k=1, which="LM")

    def predict(self, Y):
        """
        Evaluates the computed eigenfunction on given test data Y.
        Note: fit() has to be run first.

        Parameters
        ----------
        Y : np.array of shape [# testpoints, dimension]
            data array containing endpoints of trajectory simulations for each startpoint
        """

        if self.sampler is None:
            raise RuntimeError("Run fit() first to fit the model.")
        Y_embedded = self.sampler.transform(Y)
        return Y_embedded @ self.vec


def _reshape(X):
    """
    This is a temporary auxiliary function to
    bridge the transfer from old twodimensional data interface
    to threedimensional interface.
    It is used to run the old computational routines
    with the new interfaces before updating the computational
    routines for the new interface directly.

    Helper function providing reshape from three dimensional data format
    [# startpoints, # simulations per startpoint, dimension]
    to twodimensional data format
    [# startpoints * # simulations per startpoint, dimension],
    where the first dimension is dominant in # simulations per startpoint
    """
    n_startpoints = X.shape[0]
    M = X.shape[1]
    dim = X.shape[2]

    return X.swapaxes(0,1).reshape(n_startpoints * M, dim)
