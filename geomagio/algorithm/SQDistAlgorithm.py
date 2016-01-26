"""Algorithm that produces Solar Quiet (SQ), Secular Variation (SV) and
    Magnetic Disturbance (DIST).

    Algorithm for producing SQ, SV and DIST.
    This module implements Holt-Winters exponential smoothing. It predicts
    an offset-from-zero plus a "seasonal" correction given observations
    up to time t-1. Each observation's influence on the current prediction
    decreases exponentially with time according to user-supplied runtime
    configuration parameters.

    Use of fmin_l_bfgs_b to estimate parameters inspired by Andre Queiroz:
        https://gist.github.com/andrequeiroz/5888967
"""

from AlgorithmException import AlgorithmException
import numpy as np
from scipy.optimize import fmin_l_bfgs_b


def additive(yobs, m, alpha, beta, gamma, phi=1,
             yhat0=None, s0=None, l0=None, b0=None, sigma0=None,
             zthresh=6, fc=0, hstep=0):
    """Primary function for Holt-Winters smoothing/forecasting with
      damped linear trend and additive seasonal component.

    The adaptive standard deviation (sigma), multiplied by zthresh to
    determine which observations should be smoothed or ignored, is always
    updated using the latest error if a valid observation is available. This
    way, if what seemed a spike in real-time was actually a more permanent
    baseline shift, the algorithm will adjust to the new baseline once sigma
    grows enough to accommodate the errors.

    The standard deviation also updates when no obserations are present, but
    does so according to Hyndman et al (2005) prediction intervals. The result
    is a sigma that grows over gaps, and for forecasts beyond yobs[-1].

    Parameters
    ----------
    yobs : array_like
        input series to be smoothed/forecast
    m : int
        number of "seasons"
    alpha : float
        the level smoothing parameter (0<=alpha<=1).
    beta : float
        the slope smoothing parameter (0<=beta<=1).
    gamma : float
        the seasonal adjustment smoothing parameter (0<=gamma<=1).
    phi : float
        the dampening factor for slope (0<=phi<=1)
        (if None, phi will be estimated; default is 1)
    yhat0 : array_like
        initial yhats for hstep>0 (len(yhat0) == hstep)
        (if None, yhat0 will be set to NaNs)
    s0 : array_like
        initial set of seasonal adjustments
        (if None, default is [yobs[i] - a[0] for i in range(m)])
    l0 : float
        initial level (i.e., l(t-hstep))
        (if None, default is mean(yobs[0:m]))
    b0 : float
        initial slope (i.e., b(t-hstep))
        (if None, default is (mean(yobs[m:2*m]) - mean(yobs[0:m]))/m )
    sigma0 : float
        initial standard-deviation estimate (len(sigma0) == hstep+1)
        (if None, default is [sqrt(var(yobs))] * (hstep+1) )
    zthresh : int
        z-score threshold to determine whether yhat is updated by
        smoothing observations, or by simulation alone; if exceeded,
        only sigma is updated to reflect latest observation
    fc : int
        the number of steps beyond the end of yobs (the available
        observations) to forecast
    hstep : int
        the number of steps ahead to predict yhat[i]
        which forces an hstep prediction at each time step

    Returns
    -------
    yhat      : series of smoothed/forecast values (aligned with yobs(t))
    shat      : series of seasonal adjustments (aligned with yobs(t))
    sigmahat: series of time-varying standard deviations (aligned with yobs(t))
    yhat0next : use as yhat0 when function called again with new observations
    s0next    : use as s0 when function called again with new observations
    l0next    : use as l0 when function called again with new observations
    b0next    : use as b0 when function called again with new observations
    sigma0next: use as sigma0 when function called again with new observations

    alpha     : optimized alpha (if input alpha is None)
    beta      : optimized beta (if input beta is None)
    gamma     : optimized gamma (if input gamma is None)
    phi       : optimized phi (if input phi is None)
    """

    if alpha is None:
        raise AlgorithmException('alpha is required')
    if beta is None:
        raise AlgorithmException('beta is required')
    if gamma is None:
        raise AlgorithmException('gamma is required')
    if phi is None:
        raise AlgorithmException('phi is required')

    # set some default values
    if l0 is None:
        l = np.nanmean(yobs[0:int(m)])
    else:
        l = l0
        if not np.isscalar(l0):
            raise AlgorithmException("l0 must be a scalar")

    if b0 is None:
        b = (np.nanmean(yobs[m:2 * m]) - np.nanmean(yobs[0:m])) / m
        b = 0 if np.isnan(b) else b  # replace NaN with 0
    else:
        b = b0
        if not np.isscalar(b0):
            raise AlgorithmException("b0 must be a scalar")

    if yhat0 is None:
        yhat = [np.nan for i in range(hstep)]
    else:
        yhat = list(yhat0)
        if len(yhat) != hstep:
            raise AlgorithmException("yhat0 must have length %d" % hstep)

    if s0 is None:
        s = [yobs[i] - l for i in range(m)]
        s = [i if ~np.isnan(i) else 0 for i in s]  # replace NaNs with 0s
    else:
        s = list(s0)
        if len(s) != m:
            raise AlgorithmException("s0 must have length %d " % m)

    if sigma0 is None:
        sigma = [np.sqrt(np.nanvar(yobs))] * (hstep + 1)
    else:
        sigma = list(sigma0)
        if len(sigma) != (hstep + 1):
            raise AlgorithmException(
                "sigma0 must have length %d" % (hstep + 1))

    #
    # Now begin the actual Holt-Winters algorithm
    #

    # ensure mean of seasonal adjustments is zero by setting first element of
    # r equal to mean(s)
    r = [np.nanmean(s)]

    # determine sum(c^2) and phi_(j-1) for hstep "prediction interval" outside
    # of loop; initialize variables for jstep (beyond hstep) prediction
    # intervals
    sumc2_H = 1
    phiHminus1 = 0
    for h in range(1, hstep):
        phiHminus1 = phiHminus1 + phi ** (h - 1)
        sumc2_H = sumc2_H + (alpha * (1 + phiHminus1 * beta) +
                           gamma * (1 if (h % m == 0) else 0)) ** 2
    phiJminus1 = phiHminus1
    sumc2 = sumc2_H
    jstep = hstep

    # convert to, and pre-allocate numpy arrays
    yobs = np.array(yobs)
    sigma = np.concatenate((sigma, np.zeros(yobs.size + fc)))
    yhat = np.concatenate((yhat, np.zeros(yobs.size + fc)))
    r = np.concatenate((r, np.zeros(yobs.size + fc)))
    s = np.concatenate((s, np.zeros(yobs.size + fc)))

    # smooth/simulate/forecast yobs
    for i in range(len(yobs) + fc):
        # Update/append sigma for h steps ahead of i following
        # Hyndman-et-al-2005. This will be over-written if valid observations
        # exist at step i
        if jstep == hstep:
            sigma2 = sigma[i] * sigma[i]
        sigma[i + hstep + 1] = np.sqrt(sigma2 * sumc2)

        # predict h steps ahead
        yhat[i + hstep] = l + phiHminus1 * b + s[i + hstep % m]

        # determine discrepancy between observation and prediction at step i
        if i < len(yobs):
            et = yobs[i] - yhat[i]
        else:
            et = np.nan

        if (np.isnan(et) or np.abs(et) > zthresh * sigma[i]):
            # forecast (i.e., update l, b, and s assuming et==0)

            # no change in seasonal adjustments
            r[i + 1] = 0
            s[i + m] = s[i]

            # update l before b
            l = l + phi * b
            b = phi * b

            if np.isnan(et):
                # when forecasting, grow sigma=sqrt(var) like a prediction
                # interval; sumc2 and jstep will be reset with the next
                # valid observation
                phiJminus1 = phiJminus1 + phi ** jstep
                sumc2 = sumc2 + (alpha * (1 + phiJminus1 * beta) +
                                 gamma * (1 if (jstep % m == 0) else 0)) ** 2
                jstep = jstep + 1

            else:
                # still update sigma using et when et > zthresh * sigma
                # (and is not NaN)
                sigma[i + 1] = alpha * np.abs(et) + (1 - alpha) * sigma[i]
        else:
            # smooth (i.e., update l, b, and s by filtering et)

            # renormalization could occur inside loop, but we choose to
            # integrate r, and adjust a and s outside the loop to improve
            # performance.
            r[i + 1] = gamma * (1 - alpha) * et / m

            # update and append to s using equation-error formulation
            s[i + m] = s[i] + gamma * (1 - alpha) * et

            # update l and b using equation-error formulation
            l = l + phi * b + alpha * et
            b = phi * b + alpha * beta * et

            # update sigma with et, then reset prediction interval variables
            sigma[i + 1] = alpha * np.abs(et) + (1 - alpha) * sigma[i]
            sumc2 = sumc2_H
            phiJminus1 = phiHminus1
            jstep = hstep
        # endif (np.isnan(et) or np.abs(et) > zthresh * sigma[i])

    # endfor i in range(len(yobs) + fc - hstep)

    r = np.cumsum(r)
    l = l + r[-1]
    s = list(np.array(s) - np.hstack((r, np.tile(r[-1], m - 1))))

    return (yhat[:len(yobs) + fc],
            s[:len(yobs) + fc],
            sigma[1:len(yobs) + fc + 1],
            yhat[len(yobs) + fc:],
            s[len(yobs) + fc:],
            l,
            b,
            sigma[len(yobs) + fc:],
            alpha,
            beta,
            gamma)


def estimate_parameters(yobs, m, alpha=None, beta=None, gamma=None, phi=1,
        yhat0=None, s0=None, l0=None, b0=None, sigma0=None,
        zthresh=6, fc=0, hstep=0,
        alpha0=0.3, beta0=0.1, gamma0=0.1):
    """Estimate alpha, beta, and gamma parameters based on observed data.

    Parameters
    ----------
    yobs : array_like
        input series to be smoothed/forecast
    m : int
        number of "seasons"
    alpha : float
        the level smoothing parameter (0<=alpha<=1).
    beta : float
        the slope smoothing parameter (0<=beta<=1).
    gamma : float
        the seasonal adjustment smoothing parameter (0<=gamma<=1).
    phi : float
        the dampening factor for slope (0<=phi<=1)
        (if None, phi will be estimated; default is 1)
    yhat0 : array_like
        initial yhats for hstep>0 (len(yhat0) == hstep)
        (if None, yhat0 will be set to NaNs)
    s0 : array_like
        initial set of seasonal adjustments
        (if None, default is [yobs[i] - a[0] for i in range(m)])
    l0 : float
        initial level (i.e., l(t-hstep))
        (if None, default is mean(yobs[0:m]))
    b0 : float
        initial slope (i.e., b(t-hstep))
        (if None, default is (mean(yobs[m:2*m]) - mean(yobs[0:m]))/m )
    sigma0 : float
        initial standard-deviation estimate (len(sigma0) == hstep+1)
        (if None, default is [sqrt(var(yobs))] * (hstep+1) )
    zthresh : int
        z-score threshold to determine whether yhat is updated by
        smoothing observations, or by simulation alone; if exceeded,
        only sigma is updated to reflect latest observation
    fc : int
        the number of steps beyond the end of yobs (the available
        observations) to forecast
    hstep : int
        the number of steps ahead to predict yhat[i]
        which forces an hstep prediction at each time step
    alpha0 : float
        initial value for alpha.
        used only when alpha is None.
    beta0 : float
        initial value for beta.
        used only when beta is None.
    gamma0 : float
        initial value for gamma.
        used only when gamma is None.

    Returns
    -------
    alpha : float
        optimized parameter alpha (if alpha was None).
    beta : float
        optimized parameter beta (if beta was None).
    gamma : float
        optimized gamma, if gamma was None.
    rmse : float
        root-mean-squared-error for data using optimized parameters.
    """
    # if alpha/beta/gamma is specified, restrict bounds to "fix" parameter.
    boundaries = [
        (alpha, alpha) if alpha is not None else (0, 1),
        (beta, beta) if beta is not None else (0, 1),
        (gamma, gamma) if gamma is not None else (0, 1)
    ]
    initial_values = np.array([
        alpha if alpha is not None else alpha0,
        beta if beta is not None else beta0,
        gamma if gamma is not None else gamma0
    ])

    def func(params, *args):
        """Function that computes root-mean-squared-error based on current
        alpha, beta, and gamma parameters; as provided by fmin_l_bfgs_b.

        Parameters
        ----------
        params: list-like
            list containing alpha, beta, and gamma parameters to test
        """
        # extract parameters to fit
        alpha, beta, gamma = params
        # call Holt-Winters with additive seasonality
        yhat, _, _, _, _, _, _, _, _, _, _ = additive(yobs, m,
                alpha=alpha, beta=beta, gamma=gamma, l0=l0, b0=b0, s0=s0,
                zthresh=zthresh, hstep=hstep)
        # compute root-mean-squared-error of predictions
        error = np.sqrt(np.nanmean(np.square(np.subtract(yobs, yhat))))
        return error

    parameters = fmin_l_bfgs_b(func, x0=initial_values, args=(),
            bounds=boundaries, approx_grad=True)
    alpha, beta, gamma = parameters[0]
    rmse = parameters[1]
    return (alpha, beta, gamma, rmse)
