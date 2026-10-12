"""Parsing and de-duplication for the Bulk add screen.

Input is one item per line. A line starting with "#" sets the platform for the
lines after it; "Title | Platform" overrides it for one line. Blank lines are
ignored.
"""

SEPARATOR = "\t"  # joins platform and title inside a checkbox value


def parse_lines(text: str, default_platform: str = "") -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    platform = default_platform.strip()

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            platform = line.lstrip("#").strip() or default_platform.strip()
            continue

        title, line_platform = line, platform
        if "|" in line:
            title, _, override = line.rpartition("|")
            title, line_platform = title.strip(), override.strip() or platform
        if not title:
            continue

        key = (title.lower(), line_platform.lower())
        if key in seen:
            continue
        seen.add(key)
        entries.append((title, line_platform))

    return entries


def split_entry(value: str) -> tuple[str, str]:
    platform, _, title = value.partition(SEPARATOR)
    return title.strip(), platform.strip()


def existing_keys(items, uses_platform: bool) -> set[tuple[str, str]]:
    return {
        (i.name.lower().strip(), (i.attributes.get("Platform") or "").lower().strip() if uses_platform else "")
        for i in items
    }


def entry_key(title: str, platform: str, uses_platform: bool) -> tuple[str, str]:
    return (title.lower().strip(), platform.lower().strip() if uses_platform else "")
