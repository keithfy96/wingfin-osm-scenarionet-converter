"""Turn a converted dataset's metres back into latitude and longitude, with no dependency.

    from geodesy import projection_origin, aeqd_inverse

`pyproj` is a dependency of *this repo*, on Python 3.10, and everything under `tools/` that
talks to MetaDrive runs on MetaDrive's 3.8 where it is not installed. That is the only reason
this file exists - the transform itself is one this repo already performs, at
`lane_payload.py:55` and `validation_view.py:231`.

**The result is exact rather than approximate**, and that was measured rather than assumed:
`aeqd_inverse` was compared against
`pyproj.Transformer.from_crs(wkt, "EPSG:4326", always_xy=True)` over 25 points spanning
+/-900 m of junction-1's origin, and the worst disagreement was **0.000000 m**. So PROJ's
ellipsoidal `aeqd` really does solve the geodesic, and this reproduces it.

Two facts make the whole thing possible, and both are already in the file MetaDrive loads:

* `metadata.coordinate_system_wkt` - the projected CRS Stage 1 chose, an azimuthal
  equidistant projection on WGS 84 centred on the extract.
* `metadata.old_origin_in_current_coordinate` - MetaDrive re-centres every scenario on the
  ego's first position (`scenario_data_manager.py:76` passes `centralize=True`) but records
  the shift it applied (`scenario_description.py:676`). Verified on junction-1: that vector
  is `[+55.725, -75.469]` against a first ego position of `[-55.725, +75.469]`.

So the whole conversion is::

    projected = metadrive_xy - old_origin_in_current_coordinate
    latitude, longitude = aeqd_inverse(*projection_origin(wkt), *projected)
"""

from __future__ import annotations

import math
import re

# WGS 84.
SEMI_MAJOR_M = 6378137.0
FLATTENING = 1 / 298.257223563


def geodesic_direct(latitude, longitude, azimuth_deg, distance_m):
    """Vincenty's direct solution: where you arrive, setting off that way and going that far.

    Returns `(latitude, longitude)` in degrees.
    """
    a, f = SEMI_MAJOR_M, FLATTENING
    b = a * (1 - f)
    phi1 = math.radians(latitude)
    alpha1 = math.radians(azimuth_deg)
    sin_alpha1, cos_alpha1 = math.sin(alpha1), math.cos(alpha1)

    tan_u1 = (1 - f) * math.tan(phi1)
    cos_u1 = 1 / math.sqrt(1 + tan_u1 * tan_u1)
    sin_u1 = tan_u1 * cos_u1
    sigma1 = math.atan2(tan_u1, cos_alpha1)
    sin_alpha = cos_u1 * sin_alpha1
    cos_sq_alpha = 1 - sin_alpha * sin_alpha
    u_sq = cos_sq_alpha * (a * a - b * b) / (b * b)
    cap_a = 1 + u_sq / 16384 * (4096 + u_sq * (-768 + u_sq * (320 - 175 * u_sq)))
    cap_b = u_sq / 1024 * (256 + u_sq * (-128 + u_sq * (74 - 47 * u_sq)))

    sigma = distance_m / (b * cap_a)
    for _ in range(200):
        cos_2sigma_m = math.cos(2 * sigma1 + sigma)
        sin_sigma, cos_sigma = math.sin(sigma), math.cos(sigma)
        delta = cap_b * sin_sigma * (
            cos_2sigma_m
            + cap_b
            / 4
            * (
                cos_sigma * (-1 + 2 * cos_2sigma_m**2)
                - cap_b
                / 6
                * cos_2sigma_m
                * (-3 + 4 * sin_sigma**2)
                * (-3 + 4 * cos_2sigma_m**2)
            )
        )
        previous, sigma = sigma, distance_m / (b * cap_a) + delta
        if abs(sigma - previous) < 1e-15:
            break

    sin_sigma, cos_sigma = math.sin(sigma), math.cos(sigma)
    cos_2sigma_m = math.cos(2 * sigma1 + sigma)
    tmp = sin_u1 * sin_sigma - cos_u1 * cos_sigma * cos_alpha1
    phi2 = math.atan2(
        sin_u1 * cos_sigma + cos_u1 * sin_sigma * cos_alpha1,
        (1 - f) * math.sqrt(sin_alpha**2 + tmp**2),
    )
    lam = math.atan2(
        sin_sigma * sin_alpha1, cos_u1 * cos_sigma - sin_u1 * sin_sigma * cos_alpha1
    )
    cap_c = f / 16 * cos_sq_alpha * (4 + f * (4 - 3 * cos_sq_alpha))
    delta_lambda = lam - (1 - cap_c) * f * sin_alpha * (
        sigma
        + cap_c * sin_sigma * (cos_2sigma_m + cap_c * cos_sigma * (-1 + 2 * cos_2sigma_m**2))
    )
    return math.degrees(phi2), longitude + math.degrees(delta_lambda)


def aeqd_inverse(latitude, longitude, easting, northing):
    """Azimuthal-equidistant metres back to `(latitude, longitude)`.

    The whole of the projection: in an azimuthal equidistant CRS, distance and bearing from
    the origin are true by construction, so inverting it *is* the geodesic direct problem.
    """
    distance = math.hypot(easting, northing)
    if distance == 0.0:
        return latitude, longitude
    azimuth = math.degrees(math.atan2(easting, northing))
    return geodesic_direct(latitude, longitude, azimuth, distance)


def projection_origin(wkt):
    """The `(latitude, longitude)` an azimuthal-equidistant WKT is centred on, or None.

    Refuses rather than guesses, and the refusal is the point: a wrong origin does not fail,
    it silently reports a plausible position in the wrong country. The whole value of the
    dataset carrying its own `coordinate_system_wkt` is that nobody has to know or remember
    where the extract came from.
    """
    if wkt is None or "Azimuthal Equidistant" not in wkt:
        return None
    if "World Geodetic System 1984" not in wkt:
        return None
    found = {}
    for name, key in (
        ("Latitude of natural origin", "latitude"),
        ("Longitude of natural origin", "longitude"),
    ):
        match = re.search(r'PARAMETER\["' + re.escape(name) + r'",\s*(-?[\d.]+)', wkt)
        if match is None:
            return None
        found[key] = float(match.group(1))
    return found["latitude"], found["longitude"]
