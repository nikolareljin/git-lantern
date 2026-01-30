from typing import Dict, List


def _string(value) -> str:
    if value is None:
        return "-"
    return str(value)


def render_table(records: List[Dict[str, str]], columns: List[str]) -> str:
    if not records:
        return "No records."

    widths = {col: len(col) for col in columns}
    for record in records:
        for col in columns:
            widths[col] = max(widths[col], len(_string(record.get(col))))

    lines = []
    header = "  ".join(col.ljust(widths[col]) for col in columns)
    lines.append(header)
    lines.append("  ".join("-" * widths[col] for col in columns))

    for record in records:
        row = "  ".join(_string(record.get(col)).ljust(widths[col]) for col in columns)
        lines.append(row)

    return "\n".join(lines)
