"""Direct tests for ingestion/geometry.py: point-in-polygon and spherical area.

Everything else in this project is tested by dbt, which means everything else
is tested as SQL. This file is the exception, and it exists because geometry.py
is the code most able to be wrong quietly: a crossing-number test that miscounts
one edge still returns a plausible boolean, and an area formula that drops a
cosine still returns a plausible number of square kilometres. Before this file
the only thing checking any of it was `check_oracle_agrees` in boundaries.py,
which proves the two implementations in geometry.py agree with each other and
nothing at all about whether either is right.

Two conventions run through everything below.

**Coordinates are (longitude, latitude), in that order.** That is what GeoJSON
does and the reverse of how anyone says it, and the module docstring in
geometry.py calls it the single most common way to get a wrong answer here
silently. The rectangle used for the ordinary containment cases is a real San
Francisco one, deliberately not square and not centred on zero, so a swapped
pair lands in the Pacific instead of quietly passing.

**Every containment case runs through both implementations.** The scalar
`point_in_geometry` builds the test oracle in `derived_pip_sample` and the
vectorised `PreparedGeometry.contains` does the actual assignment, and ADR-6
rests on those two agreeing. The `contains` fixture below runs each case
through both, so the cases real data is least likely to contain are also the
ones where the two are compared.
"""

import numpy as np
import pytest

import geometry as geo

# A block of San Francisco: longitude runs west of -122, latitude north of 37.
# The two ranges do not overlap and are different widths, so a caller that
# passes (latitude, longitude) gets False rather than a coincidence.
SF_WEST, SF_EAST = -122.45, -122.40
SF_SOUTH, SF_NORTH = 37.75, 37.80
SF_INSIDE = (-122.425, 37.775)


def ring(west: float, south: float, east: float, north: float) -> list:
    """A closed rectangular ring in (longitude, latitude) order."""
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


def polygon(west: float, south: float, east: float, north: float) -> dict:
    return {"type": "Polygon", "coordinates": [ring(west, south, east, north)]}


SF_BLOCK = polygon(SF_WEST, SF_SOUTH, SF_EAST, SF_NORTH)


def _scalar(geometry: dict, longitude: float, latitude: float) -> bool:
    return geo.point_in_geometry(longitude, latitude, geometry)


def _vectorised(geometry: dict, longitude: float, latitude: float) -> bool:
    prepared = geo.PreparedGeometry(geometry)
    answers = prepared.contains(np.array([longitude]), np.array([latitude]))
    return bool(answers[0])


@pytest.fixture(params=[_scalar, _vectorised], ids=["scalar", "vectorised"])
def contains(request):
    """Both implementations, as one callable: contains(geometry, longitude, latitude)."""
    return request.param


# ---------------------------------------------------------------------------
# The cases with a right answer
# ---------------------------------------------------------------------------


def test_point_strictly_inside(contains):
    assert contains(SF_BLOCK, *SF_INSIDE) is True


@pytest.mark.parametrize(
    ("longitude", "latitude", "where"),
    [
        (-122.50, 37.775, "west of it"),
        (-122.35, 37.775, "east of it"),
        (-122.425, 37.70, "south of it"),
        (-122.425, 37.85, "north of it"),
        (-122.50, 37.85, "diagonally outside, so outside both bounding ranges"),
    ],
)
def test_point_strictly_outside(contains, longitude, latitude, where):
    assert contains(SF_BLOCK, longitude, latitude) is False, where


def test_swapped_coordinates_land_outside(contains):
    """(latitude, longitude) instead of (longitude, latitude) must not pass.

    This is the failure the geometry.py docstring warns about: a swapped San
    Francisco point is a real coordinate in the Pacific rather than an error,
    so nothing raises and the answer is merely wrong. The rectangle above is
    chosen so that the swap is unambiguously outside it.
    """
    longitude, latitude = SF_INSIDE
    assert contains(SF_BLOCK, latitude, longitude) is False


def test_point_inside_a_hole_is_outside(contains):
    """A hole is not part of the polygon, so a point in one is not in it."""
    holed = {
        "type": "Polygon",
        "coordinates": [ring(0.0, 0.0, 4.0, 4.0), ring(1.0, 1.0, 3.0, 3.0)],
    }
    assert contains(holed, 2.0, 2.0) is False
    # Between the hole and the exterior, which is the part of the polygon that
    # a hole implementation testing only the first ring would also return True
    # for, and a hole implementation inverting the test would return False for.
    assert contains(holed, 0.5, 0.5) is True
    assert contains(holed, 3.5, 3.5) is True


def test_multipolygon_contains_a_point_in_every_part(contains):
    """Both parts count, and the gap between them does not.

    Several San Francisco neighbourhoods are genuinely multipart (Treasure
    Island with the mainland, for one), so this is the shape the boundary data
    actually has rather than an exotic case.
    """
    islands = {
        "type": "MultiPolygon",
        "coordinates": [[ring(0.0, 0.0, 1.0, 1.0)], [ring(5.0, 5.0, 6.0, 6.0)]],
    }
    assert contains(islands, 0.5, 0.5) is True
    assert contains(islands, 5.5, 5.5) is True
    assert contains(islands, 3.0, 3.0) is False


@pytest.mark.parametrize(
    ("name", "geometry"),
    [
        ("none", None),
        ("empty dict", {}),
        ("not a polygon", {"type": "Point", "coordinates": [0.5, 0.5]}),
        ("polygon with no rings", {"type": "Polygon", "coordinates": []}),
        ("polygon with an empty ring", {"type": "Polygon", "coordinates": [[]]}),
        ("one vertex", {"type": "Polygon", "coordinates": [[[0.5, 0.5]]]}),
        ("two vertices", {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 1.0]]]}),
        (
            "three collinear vertices, so zero area",
            {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]]]},
        ),
    ],
)
def test_degenerate_geometries_contain_nothing(contains, name, geometry):
    """Nothing is inside a shape with no interior, and none of these may raise.

    DataSF ships empty geometries on a handful of boundary rows, and the
    contract geometry.py states for them is that a boundary with no geometry
    produces no cells and shows up as a zero rather than stopping the build.
    That contract is only worth anything if it also holds one layer down, which
    is what this asserts: every one of these returns False, including the two
    that are on the point being tested.
    """
    assert contains(geometry, 0.5, 0.5) is False
    assert contains(geometry, 0.0, 0.0) is False


def test_iter_polygons_yields_one_list_of_rings_per_polygon():
    """The shared entry point: everything else in the module iterates through it."""
    assert len(list(geo.iter_polygons(SF_BLOCK))) == 1
    multi = {
        "type": "MultiPolygon",
        "coordinates": [[ring(0.0, 0.0, 1.0, 1.0)], [ring(5.0, 5.0, 6.0, 6.0)]],
    }
    assert len(list(geo.iter_polygons(multi))) == 2
    # An empty part of a MultiPolygon is dropped rather than yielded as an
    # empty ring list, which is what keeps `rings[0]` safe in every caller.
    ragged = {"type": "MultiPolygon", "coordinates": [[], [ring(0.0, 0.0, 1.0, 1.0)]]}
    assert len(list(geo.iter_polygons(ragged))) == 1
    assert list(geo.iter_polygons({"type": "LineString", "coordinates": [[0, 0], [1, 1]]})) == []


# ---------------------------------------------------------------------------
# The cases with no right answer, only a contract
#
# A point exactly on an edge or exactly on a vertex has no correct answer in a
# ray-casting test. It is not a hard case that this implementation gets wrong;
# it is a question the method does not answer, and any implementation has to
# pick a side by convention. Asserting "on the edge means inside" would assert
# a property `point_in_ring` does not have, and asserting whichever way the
# floating point happens to fall today would freeze an accident into a test.
#
# So what gets asserted here is the property the callers actually depend on.
# `point_in_ring` is half-open on both axes: `(y1 > lat) != (y2 > lat)` makes an
# edge span [y_low, y_high), and `longitude < crossing_x` counts a crossing only
# strictly to the point's east. Together those give:
#
#   NEVER BOTH.    In a set of polygons that do not overlap, a point on a
#                  shared edge or vertex is claimed by at most one of them.
#                  This is the half that matters most: a point claimed twice
#                  fans out every join built on the bridge table, which is the
#                  worst failure available in this design.
#   EXACTLY ONE    on an edge or vertex interior to the covered region, so no
#                  point inside the region is dropped.
#   EITHER         on the outer perimeter of the region, where a point may be
#                  claimed by nobody. ADR-6 accepts this: the only consumers
#                  are a covering-cell classifier, where such a point is inside
#                  one of two adjacent polygons either way, and a sampled test
#                  oracle, where it is one row.
#
# The tests below assert those three, and they would still hold for a
# reimplementation that picked the opposite side of every edge. That is the
# point of writing them this way.
# ---------------------------------------------------------------------------

# Four unit squares tiling [0, 2] x [0, 2], meeting at the vertex (1, 1). This
# is a boundary set in miniature: polygons that share edges and do not overlap,
# which is what analysis_neighborhoods, supervisor_districts and
# census_block_groups all are.
TILING = {
    "south west": polygon(0.0, 0.0, 1.0, 1.0),
    "south east": polygon(1.0, 0.0, 2.0, 1.0),
    "north west": polygon(0.0, 1.0, 1.0, 2.0),
    "north east": polygon(1.0, 1.0, 2.0, 2.0),
}


def _claimants(contains, longitude: float, latitude: float) -> list[str]:
    return [name for name, shape in TILING.items() if contains(shape, longitude, latitude)]


@pytest.mark.parametrize(
    ("longitude", "latitude", "shared_by"),
    [
        (1.0, 0.5, "the vertical edge between the two southern squares"),
        (1.0, 1.5, "the vertical edge between the two northern squares"),
        (0.5, 1.0, "the horizontal edge between the two western squares"),
        (1.5, 1.0, "the horizontal edge between the two eastern squares"),
    ],
)
def test_a_point_on_a_shared_edge_belongs_to_exactly_one_polygon(
    contains, longitude, latitude, shared_by
):
    """Exactly one, and which one is not asserted because it is a convention.

    Today the half-open rules hand it to the polygon on the east side of a
    vertical edge and the north side of a horizontal one. That is a consequence
    of `<` and `>` rather than a decision anyone made, so it is recorded here
    and not asserted: a future implementation that reversed both would be
    equally correct, and this test would still pass.
    """
    assert len(_claimants(contains, longitude, latitude)) == 1, shared_by


def test_a_point_on_a_shared_vertex_belongs_to_exactly_one_polygon(contains):
    """The vertex where all four squares meet: one claimant, not zero and not four.

    Four is the failure that would double-count a point in every mart built on
    the bridge table. Zero is the failure that would silently drop it. The
    `(y1 > latitude) != (y2 > latitude)` form in `point_in_ring` is what rules
    both out, by making a ray that passes exactly through a shared vertex cross
    one of the two edges meeting there rather than both or neither.
    """
    assert len(_claimants(contains, 1.0, 1.0)) == 1


@pytest.mark.parametrize(
    ("longitude", "latitude"),
    [
        (0.0, 0.5),  # west perimeter
        (2.0, 0.5),  # east perimeter
        (0.5, 0.0),  # south perimeter
        (0.5, 2.0),  # north perimeter
        (0.0, 0.0),  # south west corner
        (2.0, 2.0),  # north east corner
    ],
)
def test_a_point_on_the_outer_perimeter_belongs_to_at_most_one_polygon(
    contains, longitude, latitude
):
    """At most one, and zero is allowed here.

    On the outside edge of the whole covered region there is no second polygon
    to fall through to, so the half-open convention that guarantees "never
    both" also means "sometimes neither". A point on the northern perimeter is
    claimed by nobody today. That is the accepted cost of the guarantee above,
    not a defect, and stating it here is what stops someone tightening this to
    `== 1` and finding out the hard way.
    """
    assert len(_claimants(contains, longitude, latitude)) <= 1


@pytest.mark.parametrize("latitude", [0.0, 0.5, 1.0, 1.5, 2.0])
@pytest.mark.parametrize("longitude", [0.0, 0.5, 1.0, 1.5, 2.0])
def test_the_two_implementations_agree_on_boundary_points(longitude, latitude):
    """Undefined does not mean unstable: both implementations must fall the same way.

    This is the property `boundaries.check_oracle_agrees` asserts on real
    sampled points every run, checked here on the points most likely to break
    it, since a real point almost never lands exactly on a vertex. If these
    ever diverge, the build fails with a resolution-sounding message about
    membership error that is really a code difference.
    """
    for shape in TILING.values():
        assert _scalar(shape, longitude, latitude) == _vectorised(shape, longitude, latitude)


def test_a_ray_grazing_two_vertices_still_classifies_correctly(contains):
    """A defined case that looks like an undefined one, and the reason the form matters.

    The point is strictly inside, but the ray cast east from it passes exactly
    through two vertices of the diamond, one on each side. If a vertex counted
    twice, or if a vertex on the ray were skipped, the parity would come out
    even and a point in the middle of the shape would be reported outside.
    """
    diamond = {
        "type": "Polygon",
        "coordinates": [[[0.0, 0.0], [1.0, 1.0], [2.0, 0.0], [1.0, -1.0]]],
    }
    assert contains(diamond, 1.0, 0.0) is True
    # And the same ray, from outside on either side, stays outside.
    assert contains(diamond, 3.0, 0.0) is False
    assert contains(diamond, -1.0, 0.0) is False


def test_a_ray_through_a_local_minimum_vertex_counts_both_its_edges(contains):
    """The other half of the vertex rule, and the case a naive fix breaks.

    This polygon is a square with a V bitten out of its top, so the vertex at
    (1, 1) is a local minimum where both edges go up. A ray cast east at that
    latitude must cross both of them, and an implementation that "fixed" the
    double-count above by skipping any vertex on the ray would cross neither
    and report this interior point as outside.
    """
    notched = {
        "type": "Polygon",
        "coordinates": [[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [1.0, 1.0], [0.0, 2.0]]],
    }
    assert contains(notched, 0.5, 1.0) is True
    assert contains(notched, 1.5, 1.0) is True
    # Above the notch and below the top of the square, so outside the polygon
    # while still inside its bounding box.
    assert contains(notched, 1.0, 1.5) is False


# ---------------------------------------------------------------------------
# Area
#
# Every expected value here comes from a closed form, computed independently of
# the implementation, and is written out with its arithmetic. A magic number in
# an area test is untestable in itself: it proves the code still does what it
# did, which is exactly what a test of a formula must not be.
# ---------------------------------------------------------------------------

# The area of a spherical quadrilateral bounded by two meridians and two
# parallels, which is the standard closed form and does not involve the
# summation `ring_area_sq_km` implements:
#
#     A = R^2 * dlon * (sin lat_north - sin lat_south)
#
# with R the Earth radius geometry.py uses, 6371.0088 km (the IUGG mean
# radius). For a one-degree square with its southern edge on the equator:
#
#     R^2      = 40589753.13 km^2
#     dlon     = pi / 180        = 0.017453292519943295 rad
#     sin(1)   - sin(0)          = 0.017452406437283512
#     A        = 12363.718145180046 km^2
#
# Near the equator rather than at San Francisco because that is where the
# closed form is easiest to check by hand against a second source: a degree of
# latitude there is about 110.574 km and a degree of longitude about 111.320 km
# on WGS84, so the answer has to be near 12309 km^2. It is 0.44 percent larger,
# which is the sphere-versus-ellipsoid difference geometry.py's docstring puts
# at about 0.3 percent worst case.
ONE_DEGREE_SQUARE_SQ_KM = 12363.718145180046
ELLIPSOIDAL_ESTIMATE_SQ_KM = 110.574 * 111.320

EQUATOR_SQUARE = ring(0.0, 0.0, 1.0, 1.0)


def test_one_degree_square_at_the_equator_matches_the_closed_form():
    assert geo.ring_area_sq_km(EQUATOR_SQUARE) == pytest.approx(ONE_DEGREE_SQUARE_SQ_KM, rel=1e-12)


def test_the_closed_form_agrees_with_an_independent_estimate():
    """Guards the number above against being transcribed wrong.

    The tight assertion is only as good as the closed form it came from, so
    this checks that closed form against a completely different source: the
    length of a degree at the equator, which is a published constant rather
    than anything derived here.
    """
    assert geo.ring_area_sq_km(EQUATOR_SQUARE) == pytest.approx(
        ELLIPSOIDAL_ESTIMATE_SQ_KM, rel=0.01
    )


def test_area_ignores_winding_order_and_whether_the_ring_is_closed():
    """Both are stated as guarantees in geometry.py and both are load bearing.

    The GeoJSON spec asks for counter-clockwise exteriors and plenty of
    publishers ignore it, so a signed area would come out negative for some
    boundaries and positive for others in the same dataset. `dim_neighborhood`
    divides by this, and a negative denominator produces a negative density
    rather than an error.
    """
    unclosed = EQUATOR_SQUARE[:-1]
    assert geo.ring_area_sq_km(unclosed) == pytest.approx(ONE_DEGREE_SQUARE_SQ_KM, rel=1e-12)
    assert geo.ring_area_sq_km(list(reversed(EQUATOR_SQUARE))) == pytest.approx(
        ONE_DEGREE_SQUARE_SQ_KM, rel=1e-12
    )


def test_geometry_area_subtracts_holes():
    # Same closed form for the hole: half a degree of longitude, between the
    # parallels at 0.25 and 0.75 degrees.
    #     A = R^2 * (0.5 * pi / 180) * (sin 0.75 - sin 0.25) = 3090.958959996550
    hole_sq_km = 3090.958959996550
    holed = {
        "type": "Polygon",
        "coordinates": [EQUATOR_SQUARE, ring(0.25, 0.25, 0.75, 0.75)],
    }
    assert geo.geometry_area_sq_km(holed) == pytest.approx(
        ONE_DEGREE_SQUARE_SQ_KM - hole_sq_km, rel=1e-12
    )


def test_geometry_area_sums_multipolygon_parts():
    two_squares = {
        "type": "MultiPolygon",
        "coordinates": [[EQUATOR_SQUARE], [ring(5.0, 0.0, 6.0, 1.0)]],
    }
    # The second square is the same size as the first: the closed form depends
    # on the latitudes and on the width in longitude, not on where in longitude
    # the shape sits.
    assert geo.geometry_area_sq_km(two_squares) == pytest.approx(
        2 * ONE_DEGREE_SQUARE_SQ_KM, rel=1e-12
    )


def test_a_ring_that_retraces_itself_has_zero_area():
    """Exactly zero, not nearly zero, because the terms cancel term by term."""
    assert geo.ring_area_sq_km([[0.0, 0.0], [1.0, 1.0]]) == 0.0
    # Every edge on one meridian, so every (lon2 - lon1) in the sum is zero.
    assert geo.ring_area_sq_km([[0.0, 0.0], [0.0, 1.0], [0.0, 2.0]]) == 0.0


def test_a_collinear_ring_is_not_zero_because_the_edges_are_not_straight_lines():
    """A trap worth a test: "degenerate in degrees" is not "zero area".

    The three vertices below are collinear in degree space, so a planar
    shoelace would return exactly zero. This formula is a trapezoid rule in
    (longitude, sin latitude), where they are not collinear, so the ring
    encloses a real sliver between the two-segment path out and the one-segment
    path back. Working the sum out by hand for (0,0), (1,1), (2,2):
    the terms are d*sin1, d*(sin1 + sin2) and -2d*sin2, so
    A = R^2 / 2 * d * (2 sin 1 - sin 2) = 1.883054158520965 km^2, with
    d = pi / 180.
    """
    collinear = [[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]]
    assert geo.ring_area_sq_km(collinear) == pytest.approx(1.883054158520965, rel=1e-12)


# ---------------------------------------------------------------------------
# The representative point, which is not the centroid
# ---------------------------------------------------------------------------


def test_representative_point_is_inside_a_shape_whose_average_vertex_is_not():
    """The reason this function exists rather than a centroid.

    Several San Francisco neighbourhoods wrap around others, and for a shape
    like that the average of the vertices falls in the bite rather than in the
    polygon. `build_boundaries` uses this to give a sub-cell-sized polygon a
    cell it can own, so a point outside the polygon would hand that boundary a
    cell belonging to its neighbour.

    The assertion is that the answer is inside, not where it is: the grid scan
    the fallback uses is an implementation detail and its step size is not.
    """
    crescent = {
        "type": "Polygon",
        "coordinates": [
            [
                [0.0, 0.0],
                [3.0, 0.0],
                [3.0, 1.0],
                [1.0, 1.0],
                [1.0, 2.0],
                [3.0, 2.0],
                [3.0, 3.0],
                [0.0, 3.0],
                [0.0, 0.0],
            ]
        ],
    }
    exterior = crescent["coordinates"][0]
    average = (
        sum(point[0] for point in exterior) / len(exterior),
        sum(point[1] for point in exterior) / len(exterior),
    )
    assert geo.point_in_geometry(average[0], average[1], crescent) is False

    representative = geo.geometry_representative_point(crescent)
    assert representative is not None
    assert geo.point_in_geometry(representative[0], representative[1], crescent) is True


def test_representative_point_of_an_empty_geometry_is_none():
    assert geo.geometry_representative_point(None) is None
    assert geo.geometry_representative_point({"type": "Polygon", "coordinates": []}) is None
