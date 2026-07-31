"""Pure-Python geometry over GeoJSON, with no geometry engine behind it.

Four functions, used by `ingestion/spatial.py` at precompute time and by
nothing at query time. That is the point: ADR-6 buys "no geometry engine when
someone runs a query" by paying for one here, once, in the smallest possible
form. Adding shapely or GEOS would work and would be faster, but it would put
a compiled dependency in the path of `make setup` on every machine, to do
about a hundred lines of work.

GeoJSON conventions this file assumes, all of which DataSF and TIGERweb
honour:

  - Coordinates are [longitude, latitude], in that order. This is the
    reverse of how coordinates are spoken and is the single most common way
    to get a wrong answer here silently, because a swapped San Francisco
    point lands in the Pacific rather than erroring.
  - A Polygon is a list of linear rings, the first being the exterior and
    the rest holes. A MultiPolygon is a list of those.
  - Rings are closed (last point equals first). Nothing here depends on it.

Winding order is deliberately not assumed. The GeoJSON spec asks for
counter-clockwise exteriors, plenty of publishers ignore it, and every
function here takes absolute values or uses a parity test so that it does not
matter.
"""

import math

import numpy as np

EARTH_RADIUS_KM = 6371.0088


def iter_polygons(geometry: dict):
    """Yield each polygon of a Polygon or MultiPolygon as a list of rings.

    Anything else, including None and the empty geometries DataSF ships for a
    handful of rows, yields nothing rather than raising. A boundary with no
    geometry should produce no cells and be visible as a zero, not stop the
    build.
    """
    if not geometry:
        return
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "Polygon":
        if coordinates:
            yield coordinates
    elif geometry_type == "MultiPolygon":
        for polygon in coordinates:
            if polygon:
                yield polygon


def point_in_ring(longitude: float, latitude: float, ring: list) -> bool:
    """Crossing-number test for one linear ring, in planar degree space.

    Casts a ray east from the point and counts edge crossings; odd means
    inside. Planar rather than spherical is correct at city scale: over the
    tens of metres between adjacent boundary vertices the great-circle path
    and the straight line in degree space differ by far less than the
    coordinate precision DataSF publishes.

    Points exactly on an edge are not defined one way or the other, and this
    returns whichever way the floating point falls. That is acceptable here
    because the only consumers are a covering-cell classifier, where such a
    point is inside one of two adjacent polygons either way, and a test
    oracle, where it is one sampled row.

    The `(y1 > latitude) != (y2 > latitude)` form is what makes a vertex count
    once rather than twice: it treats each edge as half-open in y, so a ray
    passing exactly through a shared vertex crosses one of the two edges
    meeting there and not both.
    """
    inside = False
    count = len(ring)
    for index in range(count):
        x1, y1 = ring[index][0], ring[index][1]
        x2, y2 = ring[(index + 1) % count][0], ring[(index + 1) % count][1]
        if (y1 > latitude) != (y2 > latitude):
            # x of the edge at this latitude. y2 != y1 is guaranteed by the
            # test above, so the division is safe.
            crossing_x = x1 + (latitude - y1) * (x2 - x1) / (y2 - y1)
            if longitude < crossing_x:
                inside = not inside
    return inside


def point_in_geometry(longitude: float, latitude: float, geometry: dict) -> bool:
    """Whether a point is inside a Polygon or MultiPolygon, holes respected."""
    for rings in iter_polygons(geometry):
        exterior = rings[0]
        if not point_in_ring(longitude, latitude, exterior):
            continue
        if any(point_in_ring(longitude, latitude, hole) for hole in rings[1:]):
            continue  # in the exterior but inside a hole, so not in this polygon
        return True
    return False


class PreparedGeometry:
    """A GeoJSON geometry arranged for testing many points against it at once.

    Two things make this fast enough to run over every point that lands in a
    boundary cell, which is the step that makes neighbourhood assignment exact
    rather than approximate:

      - Rings are converted to numpy arrays once, not once per point.
      - Every polygon and every ring carries a bounding box, so a point
        outside the box costs a comparison rather than a scan of a few
        hundred edges. San Francisco's neighbourhoods are long and thin
        enough that this rejects almost everything almost immediately.

    `contains` takes and returns arrays. The scalar `point_in_geometry` above
    is kept as a separate implementation on purpose: it is what builds the
    test oracle in `derived_pip_sample`, and spatial.py asserts the two agree
    on that sample. Two implementations that agree is weak evidence, but it is
    strictly more than one implementation checked against itself.
    """

    __slots__ = ("bbox", "polygons")

    def __init__(self, geometry: dict):
        self.polygons = []
        for rings in iter_polygons(geometry):
            prepared_rings = [np.asarray(ring, dtype=np.float64) for ring in rings]
            if prepared_rings[0].size == 0:
                continue
            exterior = prepared_rings[0]
            self.polygons.append(
                (
                    prepared_rings,
                    (
                        exterior[:, 0].min(),
                        exterior[:, 1].min(),
                        exterior[:, 0].max(),
                        exterior[:, 1].max(),
                    ),
                )
            )
        if self.polygons:
            self.bbox = (
                min(box[0] for _, box in self.polygons),
                min(box[1] for _, box in self.polygons),
                max(box[2] for _, box in self.polygons),
                max(box[3] for _, box in self.polygons),
            )
        else:
            self.bbox = None

    def contains(self, longitudes: np.ndarray, latitudes: np.ndarray) -> np.ndarray:
        """Boolean array: whether each point is inside, holes respected."""
        result = np.zeros(len(longitudes), dtype=bool)
        if self.bbox is None:
            return result

        in_bbox = _within(longitudes, latitudes, self.bbox)
        if not in_bbox.any():
            return result

        for rings, box in self.polygons:
            candidate = in_bbox & ~result & _within(longitudes, latitudes, box)
            if not candidate.any():
                continue
            inside = _ring_contains(longitudes, latitudes, rings[0], candidate)
            for hole in rings[1:]:
                inside &= ~_ring_contains(longitudes, latitudes, hole, inside)
            result |= inside
        return result


def _within(longitudes: np.ndarray, latitudes: np.ndarray, box) -> np.ndarray:
    return (
        (longitudes >= box[0])
        & (longitudes <= box[2])
        & (latitudes >= box[1])
        & (latitudes <= box[3])
    )


def _ring_contains(
    longitudes: np.ndarray, latitudes: np.ndarray, ring: np.ndarray, mask: np.ndarray
) -> np.ndarray:
    """Vectorised crossing-number test, identical in semantics to point_in_ring.

    One numpy pass per edge over all candidate points, rather than one Python
    pass per point over all edges. Horizontal edges are skipped, which is what
    makes the division by (y2 - y1) safe; the scalar version gets the same
    protection from its `(y1 > lat) != (y2 > lat)` guard, which is never true
    when y1 equals y2.
    """
    inside = np.zeros(len(longitudes), dtype=bool)
    count = len(ring)
    if count < 3:
        return inside
    xs, ys = longitudes, latitudes
    for index in range(count):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % count]
        if y1 == y2:
            continue
        crosses = ((y1 > ys) != (y2 > ys)) & mask
        if not crosses.any():
            continue
        crossing_x = x1 + (ys - y1) * (x2 - x1) / (y2 - y1)
        inside ^= crosses & (xs < crossing_x)
    return inside & mask


def ring_area_sq_km(ring: list) -> float:
    """Unsigned spherical area of one linear ring.

    The standard spherical excess formula for a polygon on a sphere:

        A = R^2 / 2 * sum( (lon2 - lon1) * (sin lat1 + sin lat2) )

    with longitudes in radians. Absolute value at the end, so winding order
    does not matter. This is exact on a sphere and therefore off by the
    Earth's flattening, about 0.3 percent worst case, which is far below the
    uncertainty in what a neighbourhood boundary means.

    The obvious alternative, a planar shoelace on raw degrees, is wrong by the
    cosine of the latitude, which at San Francisco is a 21 percent error on
    every area. That is the sort of mistake that produces plausible numbers.
    """
    total = 0.0
    count = len(ring)
    for index in range(count):
        lon1, lat1 = math.radians(ring[index][0]), math.radians(ring[index][1])
        lon2, lat2 = (
            math.radians(ring[(index + 1) % count][0]),
            math.radians(ring[(index + 1) % count][1]),
        )
        total += (lon2 - lon1) * (math.sin(lat1) + math.sin(lat2))
    return abs(total) * EARTH_RADIUS_KM * EARTH_RADIUS_KM / 2.0


def geometry_area_sq_km(geometry: dict) -> float:
    """Area of a Polygon or MultiPolygon in square kilometres, holes removed."""
    total = 0.0
    for rings in iter_polygons(geometry):
        total += ring_area_sq_km(rings[0])
        for hole in rings[1:]:
            total -= ring_area_sq_km(hole)
    return total


def geometry_representative_point(geometry: dict) -> tuple[float, float] | None:
    """A (longitude, latitude) known to be inside the geometry, or None.

    Not the centroid: a centroid can fall outside a crescent, and several San
    Francisco neighbourhoods wrap around others. This takes the vertex-average
    of the largest ring, keeps it if it is inside, and otherwise falls back to
    scanning a coarse grid across the bounding box for the first interior
    point. Used only as a last resort, when a polygon is too small to contain
    the centre of even one H3 cell and would otherwise get no cells at all.
    """
    polygons = list(iter_polygons(geometry))
    if not polygons:
        return None
    largest = max(polygons, key=lambda rings: ring_area_sq_km(rings[0]))
    exterior = largest[0]

    average = (
        sum(point[0] for point in exterior) / len(exterior),
        sum(point[1] for point in exterior) / len(exterior),
    )
    if point_in_geometry(average[0], average[1], geometry):
        return average

    longitudes = [point[0] for point in exterior]
    latitudes = [point[1] for point in exterior]
    steps = 16
    for row in range(1, steps):
        for column in range(1, steps):
            longitude = min(longitudes) + (max(longitudes) - min(longitudes)) * column / steps
            latitude = min(latitudes) + (max(latitudes) - min(latitudes)) * row / steps
            if point_in_geometry(longitude, latitude, geometry):
                return (longitude, latitude)
    return None
