"""Deterministic placement before rendering; never reassign an explicit specification."""
import math


def allocate_grouped_positions(rows, columns, n_groups, wells_per_group, prefer_interior=True):
    """Place equal-size ordered groups, using interior cells before the perimeter.

    Prefer centered horizontal blocks when they fit. Otherwise allow groups to
    wrap across interior rows. Use peripheral positions only when capacity needs
    them. Return one ordered list of (row, column) pairs per group.
    """
    if not isinstance(rows, list) or not rows or any(not isinstance(r, str) or not r for r in rows):
        raise ValueError('rows must be nonempty strings')
    if len(set(rows)) != len(rows):
        raise ValueError('row labels must be unique')
    if any(type(v) is not int or v <= 0 for v in (columns, n_groups, wells_per_group)):
        raise ValueError('columns, n_groups and wells_per_group must be positive integers')
    if type(prefer_interior) is not bool:
        raise ValueError('prefer_interior must be boolean')
    needed = n_groups * wells_per_group
    if needed > len(rows) * columns:
        raise ValueError('Assigned groups exceed plate capacity')
    row_indices = list(range(len(rows)))
    col_indices = list(range(1, columns + 1))
    inner_rows = row_indices[1:-1]
    inner_cols = col_indices[1:-1]
    if prefer_interior and len(inner_rows) * len(inner_cols) >= needed:
        available_rows, available_cols = inner_rows, inner_cols
    elif not prefer_interior:
        available_rows, available_cols = row_indices, col_indices
    else:
        # Exhaust the interior, then preserve adjacent groups where possible.
        interior = [(r, c) for r in inner_rows for c in inner_cols]
        edges = [(r, c) for r in row_indices for c in col_indices
                 if r in (0, len(rows)-1) or c in (1, columns)]
        selected = list(interior)
        unused = set(edges)
        while len(selected) < needed:
            count = min(wells_per_group - len(selected) % wells_per_group, needed-len(selected))
            runs = []
            for r, c in sorted(unused):
                for dr, dc in ((0, 1), (1, 0)):
                    run = [(r+dr*j, c+dc*j) for j in range(count)]
                    if all(pos in unused for pos in run):
                        runs.append(run)
            def score(run):
                adjacent = (len(selected) % wells_per_group != 0 and
                            abs(run[0][0]-selected[-1][0]) + abs(run[0][1]-selected[-1][1]) == 1)
                corners = sum(r in (0, len(rows)-1) and c in (1, columns) for r,c in run)
                return (not adjacent, corners, run)
            if runs:
                chosen = min(runs, key=score)
            else:
                chosen = sorted(unused, key=lambda rc: (
                    rc[0] in (0,len(rows)-1) and rc[1] in (1,columns), rc))[:count]
            selected.extend(chosen)
            unused.difference_update(chosen)
        return [[(rows[r], c) for r, c in selected[i*wells_per_group:(i+1)*wells_per_group]]
                for i in range(n_groups)]
    groups_per_row = min(n_groups, len(available_cols) // wells_per_group)
    if groups_per_row and math.ceil(n_groups / groups_per_row) <= len(available_rows):
        block_height = math.ceil(n_groups / groups_per_row)
        block_width = groups_per_row * wells_per_group
        r0 = (len(available_rows) - block_height) // 2
        c0 = (len(available_cols) - block_width) // 2
        return [[(rows[available_rows[r0 + i // groups_per_row]],
                  available_cols[c0 + (i % groups_per_row)*wells_per_group + j])
                 for j in range(wells_per_group)] for i in range(n_groups)]
    selected = [(r,c) for r in available_rows for c in available_cols]
    return [[(rows[r], c) for r, c in selected[i*wells_per_group:(i+1)*wells_per_group]]
            for i in range(n_groups)]
