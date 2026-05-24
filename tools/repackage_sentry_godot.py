#!/usr/bin/env python3
from __future__ import annotations

from collections.abc import Iterable


class RepackageError(RuntimeError):
    """Raised when an addon archive cannot be safely repackaged."""


def normalize_platforms(platforms: Iterable[str]) -> set[str]:
    normalized = {platform.strip().lower() for platform in platforms if platform.strip()}
    if not normalized:
        raise RepackageError("At least one platform must be requested")
    return normalized


def platform_prefix(key: str) -> str:
    return key.strip().split(".", 1)[0]


def section_name(line: str) -> str | None:
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        return stripped[1:-1].strip()
    return None


def entry_key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith(";") or stripped.startswith("#"):
        return None
    if "=" not in stripped:
        return None
    return stripped.split("=", 1)[0].strip()


def read_entry(lines: list[str], index: int) -> tuple[list[str], int]:
    entry = [lines[index]]
    depth = lines[index].count("{") - lines[index].count("}")
    index += 1

    while depth > 0 and index < len(lines):
        entry.append(lines[index])
        depth += lines[index].count("{") - lines[index].count("}")
        index += 1

    return entry, index


def mentions_removed_platform(line: str, keep_platforms: set[str]) -> bool:
    removed_tokens = {"android", "ios", "linux", "macos", "noop", "web", "windows"} - keep_platforms
    normalized_line = line.lower()
    return any(token in normalized_line for token in removed_tokens)


def filter_section_entries(lines: list[str], keep_platforms: set[str]) -> tuple[list[str], set[str]]:
    output: list[str] = []
    kept_platforms: set[str] = set()
    index = 0

    while index < len(lines):
        key = entry_key(lines[index])
        if key is None:
            if not mentions_removed_platform(lines[index], keep_platforms):
                output.append(lines[index])
            index += 1
            continue

        entry, next_index = read_entry(lines, index)
        platform = platform_prefix(key)
        if platform in keep_platforms:
            output.extend(entry)
            kept_platforms.add(platform)
        index = next_index

    return output, kept_platforms


def split_sections(text: str) -> list[tuple[str | None, list[str]]]:
    sections: list[tuple[str | None, list[str]]] = []
    current_name: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines(keepends=True):
        name = section_name(line)
        if name is not None:
            if current_lines:
                sections.append((current_name, current_lines))
            current_name = name
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_name, current_lines))

    return sections


def filter_gdextension_text(text: str, platforms: Iterable[str]) -> str:
    keep_platforms = normalize_platforms(platforms)
    output: list[str] = []
    kept_library_platforms: set[str] = set()

    for name, section_lines in split_sections(text):
        if name in {"libraries", "dependencies"}:
            header = section_lines[:1]
            body = section_lines[1:]
            filtered_body, kept_platforms = filter_section_entries(body, keep_platforms)
            if name == "libraries":
                kept_library_platforms.update(kept_platforms)
            output.extend(header)
            output.extend(filtered_body)
        else:
            output.extend(section_lines)

    missing_platforms = keep_platforms - kept_library_platforms
    if missing_platforms:
        missing = ", ".join(sorted(missing_platforms))
        raise RepackageError(f"Requested platform(s) have no library entries: {missing}")

    result = "".join(output)
    if text.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result
