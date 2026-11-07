"""Small plane/scan-line helpers. No polygon or graph-engine dependency."""
from __future__ import annotations

import numpy as np
import trimesh


def components(mesh):
    parents = np.arange(len(mesh.faces))

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    for a, b in mesh.face_adjacency:
        parents[root(a)] = root(b)
    groups = {}
    for i in range(len(parents)):
        groups.setdefault(int(root(i)), []).append(i)
    return [np.asarray(group) for group in groups.values()]


def section(mesh, axis, height, epsilon):
    normal = np.eye(3)[axis]
    segments = trimesh.intersections.mesh_plane(mesh, normal, normal * height)
    axes = [i for i in range(3) if i != axis]
    segments = segments[:, :, axes]
    if not len(segments):
        return segments, [], True
    # Quantization joins triangle edges only; never close an actually open contour.
    keys = np.round(segments / epsilon).astype(np.int64)
    edges, coordinates, adjacency = [], {}, {}
    for pair, points in zip(keys, segments):
        a, b = map(tuple, pair)
        if a == b:
            continue
        index = len(edges)
        edges.append((a, b))
        for key, point in zip((a, b), points):
            coordinates[key] = point
            adjacency.setdefault(key, []).append(index)
    closed = all(len(v) == 2 for v in adjacency.values())
    loops, used = [], set()
    for index, (start, _) in enumerate(edges):
        if index in used:
            continue
        current, loop = start, []
        for _ in range(len(edges) + 1):
            loop.append(coordinates[current])
            choices = [e for e in adjacency[current] if e not in used]
            if not choices:
                break
            edge = choices[0]
            used.add(edge)
            a, b = edges[edge]
            current = b if a == current else a
            if current == start:
                if len(loop) >= 3:
                    loops.append(np.asarray(loop))
                break
    return segments, loops, closed


def contains(points, loop):
    points = np.atleast_2d(points)
    a, b = loop, np.roll(loop, -1, axis=0)
    x, y = points[:, 0, None], points[:, 1, None]
    dy = b[:, 1] - a[:, 1]
    crossing = (a[:, 1] > y) != (b[:, 1] > y)
    intercept = a[:, 0] + (y - a[:, 1]) * (b[:, 0] - a[:, 0]) / np.where(dy == 0, 1, dy)
    return np.count_nonzero(crossing & (x < intercept), axis=1) % 2 == 1


def solid_contains(points, loops):
    result = np.zeros(len(np.atleast_2d(points)), dtype=bool)
    for loop in loops:
        result ^= contains(points, loop)
    return result


def outer_loops(loops):
    return [loop for i, loop in enumerate(loops)
            if sum(bool(contains(loop[0], other)[0]) for j, other in enumerate(loops) if i != j) % 2 == 0]


def intervals(segments, coordinate, axis=0):
    """Filled intervals along axis, with a half-open rule at shared vertices."""
    other = 1 - axis
    a, b = segments[:, 0], segments[:, 1]
    mask = (a[:, other] > coordinate) != (b[:, other] > coordinate)
    a, b = a[mask], b[mask]
    if not len(a):
        return np.empty((0, 4))
    values = a[:, axis] + (coordinate - a[:, other]) * (b[:, axis] - a[:, axis]) / (b[:, other] - a[:, other])
    alignment = np.abs(b[:, other]-a[:, other])/np.linalg.norm(b-a, axis=1)
    order = np.argsort(values)
    values, alignment = values[order], alignment[order]
    if len(values) % 2:
        return None
    return np.c_[values.reshape(-1, 2), alignment.reshape(-1, 2)]


def merge_intervals(intervals):
    """Union solids while preserving the part identities at each free-space boundary."""
    merged = []
    for lo, hi, part, left_alignment, right_alignment in sorted(intervals):
        if hi - lo <= 1e-9:
            continue
        if merged and lo <= merged[-1][1] + 1e-9:
            if hi > merged[-1][1]:
                merged[-1][1], merged[-1][3], merged[-1][5] = hi, part, right_alignment
        else:
            merged.append([lo, hi, part, part, left_alignment, right_alignment])
    return merged


def segment_distance(a, b, points):
    delta = b - a
    t = np.clip(((points - a) @ delta) / max(float(delta @ delta), 1e-20), 0, 1)
    return np.linalg.norm(points - (a + t[:, None] * delta), axis=1)


def overlaps(loop, previous):
    """Polygon overlap including crossing edges; containment alone misses crossed thin strips."""
    if not previous:
        return False
    points = np.concatenate((loop, (loop + np.roll(loop, -1, axis=0)) / 2))
    if solid_contains(points, previous).any():
        return True
    for other in previous:
        if contains(other, loop).any():
            return True
        for a, b in zip(loop, np.roll(loop, -1, axis=0)):
            c, d = other, np.roll(other, -1, axis=0)
            r, s = b - a, d - c
            denom = r[0] * s[:, 1] - r[1] * s[:, 0]
            valid = np.abs(denom) > 1e-12
            offset = c - a
            t = np.divide(offset[:, 0] * s[:, 1] - offset[:, 1] * s[:, 0], denom,
                          out=np.full(len(c), np.inf), where=valid)
            u = np.divide(offset[:, 0] * r[1] - offset[:, 1] * r[0], denom,
                          out=np.full(len(c), np.inf), where=valid)
            if ((t >= 0) & (t <= 1) & (u >= 0) & (u <= 1)).any():
                return True
    return False
