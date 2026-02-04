import shutil
from typing import Dict, List


def _string(value) -> str:
    if value is None:
        return "-"
    return str(value)


def _truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return value[: width - 3] + "..."


def _fit_widths(columns: List[str], widths: Dict[str, int], max_width: int) -> Dict[str, int]:
    def total_width(values: Dict[str, int]) -> int:
        active = [width for width in values.values() if width > 0]
        if not active:
            return 0
        return sum(active) + (2 * (len(active) - 1))

    total = total_width(widths)
    if total <= max_width:
        return widths

    min_widths = {col: 1 for col in columns}
    reducible = {col for col in columns if widths[col] > min_widths[col]}

    while total > max_width and reducible:
        excess = total - max_width
        per_col = max(1, excess // len(reducible))
        for col in sorted(reducible, key=lambda c: widths[c], reverse=True):
            if excess <= 0:
                break
            possible = widths[col] - min_widths[col]
            if possible <= 0:
                reducible.discard(col)
                continue
            delta = min(per_col, possible, excess)
            if delta <= 0:
                continue
            widths[col] -= delta
            total -= delta
            excess -= delta
            if widths[col] <= min_widths[col]:
                reducible.discard(col)
        if not reducible:
            break
    if total > max_width:
        for col in columns:
            widths[col] = 1
        total = total_width(widths)
        remaining = list(columns)
        while total > max_width and remaining:
            widths[remaining.pop()] = 0
            total = total_width(widths)
    return widths


def render_table(records: List[Dict[str, str]], columns: List[str]) -> str:
    if not records:
        return "No records."

    widths = {col: len(col) for col in columns}
    for record in records:
        for col in columns:
            widths[col] = max(widths[col], len(_string(record.get(col))))

    term_width = shutil.get_terminal_size((120, 20)).columns
    widths = _fit_widths(columns, widths, term_width)

    lines = []
    header = "  ".join(
        _truncate(col, widths[col]).ljust(widths[col])
        for col in columns
        if widths[col] > 0
    )
    lines.append(header)
    lines.append("  ".join("-" * widths[col] for col in columns if widths[col] > 0))

    for record in records:
        row = "  ".join(
            _truncate(_string(record.get(col)), widths[col]).ljust(widths[col])
            for col in columns
            if widths[col] > 0
        )
        lines.append(row)

    return "\n".join(lines)
