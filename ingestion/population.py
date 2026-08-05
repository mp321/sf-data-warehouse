"""Population and housing units, interpolated from block groups onto H3 cells.

One derived table, `derived_h3_population`, one row per (resolution, cell). It
is the denominator half of the project's question: a raw count per area is
mostly a map of where people live, so every count mart in this warehouse has a
normalised companion measure, and this is what those companions divide by.

The interpolation, and what it is allowed to claim, is in the docstring below.
"""

from h3_points import RESOLUTIONS


def build_h3_population(boundary_rows: list[dict], bridge_rows: list[dict]) -> list[dict]:
    """derived_h3_population: block group population spread over H3 cells.

    Areal interpolation under the standard assumption that population is
    uniform within a block group. Each block group's centre-contained cells
    partition its area, so its population is divided equally among them. That
    is exactly as wrong as the uniformity assumption and no more: a block
    group covering a park and a tower block will smear residents across the
    park.

    Centre-contained rather than covering cells, because centre-contained
    cells tile without overlapping. Interpolating over covering cells would
    count the population of every edge block group once per neighbour it
    touches, and the city total would come out well above 873,965.

    It reads `is_allocation_cell` rather than `is_primary`, and the difference
    is the whole reason both columns exist. `is_primary` is stripped down to
    one boundary per cell so that membership joins cannot fan out. At r8 a
    single cell covers dozens of block groups, so stripping it discards all
    but one of them, and with them their population: measured at 221,088 of
    873,965 residents surviving to r8 before this was split out. Conservation
    is checked below rather than trusted.
    """
    measures = {
        row["boundary_id"]: (row["population"], row["housing_units"])
        for row in boundary_rows
        if row["boundary_set"] == "census_block_group"
    }
    if not measures:
        return []

    cells_by_boundary: dict[tuple, list[int]] = {}
    for row in bridge_rows:
        if row["boundary_set"] != "census_block_group" or not row["is_allocation_cell"]:
            continue
        cells_by_boundary.setdefault((row["boundary_id"], row["resolution"]), []).append(
            row["h3_cell"]
        )

    totals: dict[tuple, list[float]] = {}
    for (boundary_id, resolution), cells in cells_by_boundary.items():
        population, housing_units = measures.get(boundary_id, (None, None))
        share_population = (population or 0) / len(cells)
        share_housing = (housing_units or 0) / len(cells)
        for cell in cells:
            bucket = totals.setdefault((resolution, cell), [0.0, 0.0])
            bucket[0] += share_population
            bucket[1] += share_housing

    expected = sum(population or 0 for population, _ in measures.values())
    for resolution in RESOLUTIONS:
        allocated = sum(
            values[0]
            for (cell_resolution, _), values in totals.items()
            if cell_resolution == resolution
        )
        if abs(allocated - expected) > 1.0:
            # Loud rather than logged. Every rate in every mart divides by
            # this, so losing residents here understates nothing and
            # overstates every per-capita number in the warehouse, silently
            # and plausibly.
            raise RuntimeError(
                f"population not conserved at r{resolution}: allocated {allocated:.0f} "
                f"of {expected} residents. A block group is contributing no cells."
            )

    return [
        {
            "resolution": resolution,
            "h3_cell": cell,
            "population": values[0],
            "housing_units": values[1],
        }
        for (resolution, cell), values in sorted(totals.items())
    ]
