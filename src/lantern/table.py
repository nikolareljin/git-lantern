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
    total = sum(widths[col] for col in columns) + (2 * (len(columns) - 1))
    if total <= max_width:
        return widths

    min_widths = {col: max(len(col), 4) for col in columns}
    reducible = {col for col in columns if widths[col] > min_widths[col]}

    while total > max_width and reducible:
        for col in sorted(reducible, key=lambda c: widths[c], reverse=True):
            if total <= max_width:
                break
            if widths[col] > min_widths[col]:
                widths[col] -= 1
                total -= 1
            if widths[col] <= min_widths[col]:
                reducible.discard(col)
        if not reducible:
            break
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
    header = "  ".join(_truncate(col, widths[col]).ljust(widths[col]) for col in columns)
    lines.append(header)
    lines.append("  ".join("-" * widths[col] for col in columns))

    for record in records:
        row = "  ".join(
            _truncate(_string(record.get(col)), widths[col]).ljust(widths[col])
            for col in columns
        )
        lines.append(row)

    return "\n".join(lines)
