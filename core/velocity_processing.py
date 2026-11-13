"""Dealiasing of archive WSR-88D radial velocity, one decoded sweep at a time
(m/s, rays sorted by azimuth).

Region-based unfolding, the method of Py-ART's ``dealias_region_based`` with
MESO-VIEW's settings (centered, rays wrap around), written with
``scipy.ndimage`` so Py-ART isn't a dependency:

1. split the Nyquist interval into a few bands and label the connected
   regions of each band;
2. find the edges between neighboring regions (gates next to each other, or
   across a gap of missing gates up to ``skip_*`` long) and the mean velocity
   difference across each;
3. merge regions along the edge with the most gates first, unfolding the
   smaller side by the whole number of Nyquist intervals (2 x Nyquist) that
   best matches its neighbor;
4. shift the whole field by whole intervals so its mean unfolding is near 0.

Callers keep the raw velocity when there is no Nyquist velocity recorded or
when ``dealias`` raises, and say so: a raw field is never labeled dealiased.
The input is never changed.
"""
from __future__ import annotations

import heapq

import numpy as np
from scipy import ndimage

# Py-ART's defaults
INTERVAL_SPLITS = 3
SKIP_BETWEEN_RAYS = 100
SKIP_ALONG_RAY = 100


def sweep_nyquist(sweep) -> float | None:
    """Nyquist velocity (m/s) of a MetPy Level2File sweep, if recorded."""
    for radial in sweep:
        consts = getattr(radial, "radial_consts", None)          # message 31
        value = getattr(consts, "nyq_vel", None) if consts is not None else None
        if value is None:
            header = radial.header if hasattr(radial, "header") else radial[0]
            value = getattr(header, "nyq_vel", None)             # message 1
        if value is not None and np.isfinite(value) and value > 0:
            return float(value)
    return None


def dealias(vel_ms: np.ndarray, nyquist_ms: float, *,
            interval_splits: int = INTERVAL_SPLITS,
            skip_between_rays: int = SKIP_BETWEEN_RAYS,
            skip_along_ray: int = SKIP_ALONG_RAY,
            centered: bool = True, rays_wrap_around: bool = True) -> np.ndarray:
    """Region-based dealiased copy of one sweep (rays x gates, m/s; NaN where
    there's no echo). Raises ValueError when there's nothing to dealias."""
    vel = np.asarray(vel_ms, dtype=np.float64)
    if vel.ndim != 2:
        raise ValueError("expected a rays x gates sweep")
    nyquist = float(nyquist_ms)
    if not np.isfinite(nyquist) or nyquist <= 0:
        raise ValueError(f"bad Nyquist velocity {nyquist_ms!r}")
    valid = np.isfinite(vel)
    if not valid.any():
        raise ValueError("no velocity gates")
    interval = 2.0 * nyquist

    labels, n_regions = _label_bands(vel, valid, nyquist, interval_splits)
    sizes = np.bincount(labels.ravel(), minlength=n_regions + 1)
    a, b, count, total = _region_edges(
        vel, valid, labels, skip_between_rays, skip_along_ray, rays_wrap_around)
    folds = _merge_regions(n_regions, sizes, a, b, count, total / interval)

    gate_folds = folds[labels]
    if centered:
        gate_folds -= int(np.round(gate_folds[valid].mean()))
    out = (vel + gate_folds * interval).astype(np.float32)
    out[~valid] = np.nan                                         # never invent gates
    return out


def _label_bands(vel, valid, nyquist, splits):
    """Connected regions within each band of the Nyquist interval; labels
    run 1..n over all bands, 0 where there's no echo."""
    edges = np.linspace(-nyquist, nyquist, splits + 1)[1:-1]
    band = np.digitize(np.where(valid, vel, 0.0), edges)        # out-of-range values join the end bands
    labels = np.zeros(vel.shape, dtype=np.int64)
    n_regions = 0
    for i in range(splits):
        in_band = valid & (band == i)
        band_labels, n = ndimage.label(in_band)
        labels[in_band] = band_labels[in_band] + n_regions
        n_regions += n
    return labels, n_regions


def _gate_pairs(valid, skip):
    """(first, second) flat indices of consecutive echo gates along each row
    of ``valid``, at most ``skip`` missing gates apart."""
    n_cols = valid.shape[1]
    idx = np.flatnonzero(valid)
    rows, cols = np.divmod(idx, n_cols)
    near = (rows[1:] == rows[:-1]) & (cols[1:] - cols[:-1] <= skip + 1)
    return idx[:-1][near], idx[1:][near]


def _region_edges(vel, valid, labels, skip_between_rays, skip_along_ray, wrap):
    """Edges between different regions: region pairs (a < b), the number of
    gate pairs joining them and the sum of (velocity in b - velocity in a)."""
    n_rays, n_gates = vel.shape
    flat_vel = vel.ravel()
    flat_labels = labels.ravel()

    first, second = _gate_pairs(valid, skip_along_ray)
    # between rays: the same along the transposed sweep, mapped back
    t_first, t_second = _gate_pairs(valid.T, skip_between_rays)
    pairs_first = [first, (t_first % n_rays) * n_gates + t_first // n_rays]
    pairs_second = [second, (t_second % n_rays) * n_gates + t_second // n_rays]
    if wrap and n_rays > 2:
        # last echo ray to first echo ray of each gate, across north
        has = valid.any(axis=0)
        first_ray = np.argmax(valid, axis=0)
        last_ray = n_rays - 1 - np.argmax(valid[::-1], axis=0)
        cols = np.flatnonzero(has & (first_ray + n_rays - last_ray <= skip_between_rays + 1)
                              & (last_ray > first_ray))
        pairs_first.append(last_ray[cols] * n_gates + cols)
        pairs_second.append(first_ray[cols] * n_gates + cols)
    first = np.concatenate(pairs_first)
    second = np.concatenate(pairs_second)

    la, lb = flat_labels[first], flat_labels[second]
    diff = flat_vel[second] - flat_vel[first]
    keep = la != lb
    la, lb, diff = la[keep], lb[keep], diff[keep]
    swap = la > lb
    la, lb = np.where(swap, lb, la), np.where(swap, la, lb)
    diff = np.where(swap, -diff, diff)
    key = la * (int(labels.max()) + 1) + lb
    unique, inverse = np.unique(key, return_inverse=True)
    count = np.bincount(inverse).astype(np.int64)
    total = np.bincount(inverse, weights=diff)
    width = int(labels.max()) + 1
    return unique // width, unique % width, count, total


def _merge_regions(n_regions, sizes, a, b, count, total_folds):
    """Merge regions along their strongest edges; returns each label's
    unfolding in Nyquist intervals (index 0, no echo, stays 0).

    ``total_folds`` per edge is sum(v_b - v_a) / interval. Each merged set
    keeps its member labels and its edges to other sets, as
    {neighbor: [gate pairs, sum of (v_neighbor - v_set) / interval]}."""
    folds = np.zeros(n_regions + 1, dtype=np.int64)
    members = {r: [r] for r in range(1, n_regions + 1)}
    gates = {r: int(sizes[r]) for r in range(1, n_regions + 1)}
    adjacency: dict[int, dict[int, list]] = {r: {} for r in range(1, n_regions + 1)}
    heap = []
    for ra, rb, c, s in zip(a.tolist(), b.tolist(), count.tolist(), total_folds.tolist()):
        adjacency[ra][rb] = [c, s]
        adjacency[rb][ra] = [c, -s]
        heap.append((-c, ra, rb))
    heapq.heapify(heap)

    while heap:
        neg_count, ra, rb = heapq.heappop(heap)
        edge = adjacency.get(ra, {}).get(rb)
        if edge is None or edge[0] != -neg_count:
            continue                                             # merged or superseded
        shift = int(np.round(edge[1] / edge[0]))
        if gates[ra] >= gates[rb]:
            base, merged, shift = ra, rb, -shift                 # bring rb down to ra
        else:
            base, merged = rb, ra
        if shift:
            for r in members[merged]:
                folds[r] += shift
            for neighbor, e in adjacency[merged].items():
                e[1] -= e[0] * shift
                adjacency[neighbor][merged][1] += e[0] * shift
        del adjacency[base][merged]
        for neighbor, (c, s) in adjacency.pop(merged).items():
            if neighbor == base:
                continue
            del adjacency[neighbor][merged]
            e = adjacency[base].setdefault(neighbor, [0, 0.0])
            e[0] += c
            e[1] += s
            adjacency[neighbor][base] = [e[0], -e[1]]
            heapq.heappush(heap, (-e[0], min(base, neighbor), max(base, neighbor)))
        members[base].extend(members.pop(merged))
        gates[base] += gates.pop(merged)
    return folds
