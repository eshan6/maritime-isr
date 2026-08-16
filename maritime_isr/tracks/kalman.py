"""Constant-velocity Kalman filter + RTS smoother on a local metric plane,
with *explicit uncertainty growth* over time since last report (roadmap 2.1).

The uncertainty cone is the core input to Phase 3 matching, so its contract
is stated here once and enforced in code:

    radius_95(dt) = min( kalman 95% radius after dt of pure prediction,
                         MAX_FEASIBLE_SPEED × dt )

The physical cap dominates after long silences — a CV covariance grows
super-linearly and would gate in half the ocean after a 9-hour dark period;
no ship outruns 60 kn. Phase 3 must call `TrackState.uncertainty_radius_m`,
never read the covariance directly.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..config import MAX_FEASIBLE_SPEED_KN

KN_TO_MS = 0.514444
EARTH_M_PER_DEG = 111_320.0

# Process noise: white-accel PSD. 0.05 m/s² std covers merchant course-keeping;
# fishing-vessel maneuvering shows up as larger innovations and is absorbed by
# the smoother rather than a per-class model we can't justify yet.
SIGMA_ACCEL = 0.05
# AIS position quality ≈ GPS: ~10 m 1-σ. Reports flagged noisy get 10×.
SIGMA_MEAS_M = 10.0
SIGMA_MEAS_NOISY_M = 100.0


def to_local(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    """Equirectangular projection about (lat0, lon0). Sub-0.1% distortion at
    track-segment scale in the AOI; per-segment anchoring keeps it honest."""
    x = (lon - lon0) * EARTH_M_PER_DEG * math.cos(math.radians(lat0))
    y = (lat - lat0) * EARTH_M_PER_DEG
    return x, y


def to_geo(x: float, y: float, lat0: float, lon0: float) -> tuple[float, float]:
    lat = lat0 + y / EARTH_M_PER_DEG
    lon = lon0 + x / (EARTH_M_PER_DEG * math.cos(math.radians(lat0)))
    return lat, lon


def _F(dt: float) -> np.ndarray:
    return np.array([[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]], float)


def _Q(dt: float) -> np.ndarray:
    q = SIGMA_ACCEL ** 2
    dt2, dt3 = dt * dt, dt * dt * dt
    return q * np.array([
        [dt3 / 3, 0, dt2 / 2, 0],
        [0, dt3 / 3, 0, dt2 / 2],
        [dt2 / 2, 0, dt, 0],
        [0, dt2 / 2, 0, dt],
    ])


_H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], float)


@dataclass
class TrackState:
    """Filtered state at a moment in time — what Phase 3 gates against."""
    t: float                 # epoch seconds
    x: np.ndarray            # [x, y, vx, vy] in local metric frame
    P: np.ndarray            # 4×4 covariance
    lat0: float
    lon0: float

    @property
    def latlon(self) -> tuple[float, float]:
        return to_geo(self.x[0], self.x[1], self.lat0, self.lon0)

    @property
    def sog_kn(self) -> float:
        return math.hypot(self.x[2], self.x[3]) / KN_TO_MS

    @property
    def cog_deg(self) -> float:
        return math.degrees(math.atan2(self.x[2], self.x[3])) % 360.0

    def predict(self, t: float) -> "TrackState":
        dt = max(0.0, t - self.t)
        F = _F(dt)
        return TrackState(t, F @ self.x, F @ self.P @ F.T + _Q(dt),
                          self.lat0, self.lon0)

    def retrodict(self, t: float) -> "TrackState":
        """Run the model BACKWARD to a time before this state.

        Constant velocity is time-symmetric, so the same transition matrix works
        with a negative interval; only the process-noise term needs its
        magnitude, since uncertainty grows in both directions away from a fix.
        """
        dt = min(0.0, t - self.t)
        F = _F(dt)
        return TrackState(t, F @ self.x, F @ self.P @ F.T + _Q(abs(dt)),
                          self.lat0, self.lon0)

    def uncertainty_radius_m(self, t: float | None = None) -> float:
        """95% position-uncertainty radius, physically capped. THE Phase 3 API."""
        s = self.predict(t) if t is not None and t > self.t else self
        dt = (t - self.t) if t is not None else 0.0
        eigs = np.linalg.eigvalsh(s.P[:2, :2])
        kalman_r95 = 2.4477 * math.sqrt(max(eigs.max(), 0.0))  # 95% for 2-D Gaussian
        cone = MAX_FEASIBLE_SPEED_KN * KN_TO_MS * max(dt, 0.0)
        return min(kalman_r95, cone) if dt > 0 else kalman_r95


def bridge(before: TrackState, after: TrackState, t: float) -> TrackState:
    """The state at `t` between two known fixes — a bridge, not an extrapolation.

    **This is the difference between "where might she have got to" and "where
    was she".** `predict` runs forward from the last report and its uncertainty
    grows without bound: over a fifty-minute AIS gap the cone opens to
    kilometres. But in a batch run the report on the *far* side of the gap is
    already in hand and already smoothed. A vessel that was at A and then at B
    was, in between, on the line joining them — the only question is how far she
    strayed from it.

    So the position is interpolated between the two fixes and the covariance is
    a **bridge**: it is small at both ends, largest in the middle, and even
    there it is bounded by how far the vessel could have wandered off the direct
    path and come back. The effective process-noise interval is `T·f·(1−f)`,
    which is zero at each fix and a quarter of the gap at the midpoint —
    a Brownian-bridge variance, and the standard result for a path pinned at
    both ends.

    Measured on the radar picture: an anchored merchant with receipts fifty
    minutes apart had a forward-predicted 95% radius of 4,120 m at the midpoint
    and a bridged one of 1,450 m. That is the whole difference between "this
    contact could be any of the fifteen ships in the anchorage" and "this
    contact is her".
    """
    T = after.t - before.t
    if T <= 0:
        return before.predict(t)
    f = min(1.0, max(0.0, (t - before.t) / T))
    x = (1.0 - f) * before.x + f * after.x
    # Endpoint uncertainty carries over quadratically — at f=0 the bridge is
    # exactly the earlier fix, at f=1 exactly the later one.
    P = ((1.0 - f) ** 2) * before.P + (f ** 2) * after.P + _Q(T * f * (1.0 - f))
    return TrackState(t, x, P, before.lat0, before.lon0)


def filter_smooth(times: np.ndarray, lats: np.ndarray, lons: np.ndarray,
                  noisy: np.ndarray | None = None,
                  sigma_m: np.ndarray | None = None,
                  ) -> tuple[list[TrackState], np.ndarray]:
    """Forward Kalman filter + Rauch–Tung–Striebel smoother over one segment.
    Returns smoothed states and per-point 1-σ position uncertainty (m).
    `noisy[i]` inflates that report's measurement noise instead of dropping it —
    raw is immutable, downweighting is the honest treatment.

    `sigma_m[i]` supplies the report's **own** 1-σ position error when the
    sensor reports one. AIS does not — every fix is a GNSS solution of roughly
    the same quality, which is why `SIGMA_MEAS_M` was a module constant. A
    coastal radar does: its cross-range error grows linearly with range, so a
    plot at 40 km is four times worse than the same target at 10 km, and
    smoothing them as equals throws away the sensor's best information about
    itself. When given, it overrides the constant; `noisy` still applies on top,
    so a flagged report is downweighted relative to its own stated accuracy.
    """
    n = len(times)
    lat0, lon0 = float(lats[0]), float(lons[0])
    zs = np.array([to_local(lats[i], lons[i], lat0, lon0) for i in range(n)])
    if noisy is None:
        noisy = np.zeros(n, bool)
    if sigma_m is not None:
        sigma_m = np.asarray(sigma_m, float)
        # A sensor that reports zero or a NaN accuracy is reporting nothing
        # useful; fall back rather than divide the filter by zero.
        sigma_m = np.where(np.isfinite(sigma_m) & (sigma_m > 0.0),
                           sigma_m, SIGMA_MEAS_M)

    def _r(i: int) -> float:
        base = SIGMA_MEAS_M if sigma_m is None else float(sigma_m[i])
        return (base * 10.0 if noisy[i] else base) ** 2

    # init: position = first fix, velocity from first pair if available
    x = np.zeros(4)
    x[:2] = zs[0]
    if n > 1 and times[1] > times[0]:
        x[2:] = (zs[1] - zs[0]) / (times[1] - times[0])
    P = np.diag([_r(0)] * 2 + [(5 * KN_TO_MS) ** 2] * 2)

    xs_f, Ps_f, xs_p, Ps_p = [], [], [], []
    t_prev = times[0]
    for i in range(n):
        dt = times[i] - t_prev
        F = _F(dt)
        xp, Pp = F @ x, F @ P @ F.T + _Q(dt)
        r = _r(i)
        R = np.diag([r, r])
        S = _H @ Pp @ _H.T + R
        K = Pp @ _H.T @ np.linalg.inv(S)
        x = xp + K @ (zs[i] - _H @ xp)
        P = (np.eye(4) - K @ _H) @ Pp
        xs_f.append(x.copy()); Ps_f.append(P.copy())
        xs_p.append(xp); Ps_p.append(Pp)
        t_prev = times[i]

    # RTS backward pass
    xs_s = [None] * n
    Ps_s = [None] * n
    xs_s[-1], Ps_s[-1] = xs_f[-1], Ps_f[-1]
    for i in range(n - 2, -1, -1):
        F = _F(times[i + 1] - times[i])
        C = xs_f[i] is not None and Ps_f[i] @ F.T @ np.linalg.inv(Ps_p[i + 1])
        xs_s[i] = xs_f[i] + C @ (xs_s[i + 1] - xs_p[i + 1])
        Ps_s[i] = Ps_f[i] + C @ (Ps_s[i + 1] - Ps_p[i + 1]) @ C.T

    states = [TrackState(float(times[i]), xs_s[i], Ps_s[i], lat0, lon0)
              for i in range(n)]
    sigma = np.array([math.sqrt(max(np.trace(Ps_s[i][:2, :2]) / 2, 0.0))
                      for i in range(n)])
    return states, sigma


def epoch_s(ts_series) -> "np.ndarray":
    """Timestamps → float epoch seconds, robust to pandas storage resolution
    (us vs ns). The naive astype('int64')/1e9 silently mis-scales on
    timestamp[us] columns — a 1000× dt error that atomizes every track."""
    import pandas as pd
    return ((pd.Series(ts_series) - pd.Timestamp("1970-01-01", tz="UTC"))
            / pd.Timedelta("1s")).to_numpy()
