"""Comment-preserving, minimal TOML editing.

The Python standard library can read TOML (``tomllib``) but not write it, and
this project has a hard "no third party runtime dependencies" rule.  Rewriting
the whole configuration from a parsed model would also destroy the user's
comments and formatting, which is unacceptable for a hand-maintained file that
is edited while the application is running.

This module therefore edits the *text* surgically:

* it finds a section header and an assignment inside that section;
* it replaces exactly the lines that belong to that assignment;
* it inserts missing keys / sections without touching anything else.

Only the value kinds the application writes are supported (booleans, integers,
floats, strings, arrays of strings and arrays of inline tables).  Writes are
atomic (temporary file + :func:`os.replace`) so a crash can never leave a
truncated configuration behind.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from screenpainter.errors import ConfigWriteError

__all__ = ["TomlDocument", "format_value"]

_HEADER_RE = re.compile(r"^[ \t]*\[\[?(?P<path>[^\[\]]+?)\]\]?[ \t]*(?:#.*)?$")
_ASSIGNMENT_RE = re.compile(
    r"^[ \t]*(?P<key>[A-Za-z0-9_\-]+|\"[^\"]*\"|'[^']*')[ \t]*=[ \t]*(?P<value>.*)$"
)
_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_ARRAY_SECTION_HEADER_RE = re.compile(r"^[ \t]*\[\[")
_CONTROL_CHARS = {chr(code): f"\\u{code:04x}" for code in range(0x20)}


class _ScanState:
    """Tracks enough TOML syntax to know whether a line starts at top level."""

    __slots__ = ("depth", "quote", "escape")

    def __init__(self) -> None:
        self.depth = 0
        self.quote: str | None = None
        self.escape = False

    def at_top_level(self) -> bool:
        return self.depth == 0 and self.quote is None

    def scan(self, text: str) -> None:
        for character in text:
            if self.quote is not None:
                if self.quote == '"':
                    if self.escape:
                        self.escape = False
                    elif character == "\\":
                        self.escape = True
                    elif character == '"':
                        self.quote = None
                elif character == "'":
                    self.quote = None
                continue
            if character == "#":
                return  # rest of the line is a comment
            if character in "\"'":
                self.quote = character
            elif character in "[{(":
                self.depth += 1
            elif character in "]})" and self.depth > 0:
                self.depth -= 1


def format_value(value: Any) -> str:
    """Render one TOML scalar / array value."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value == int(value):
            return f"{value:.1f}"
        return repr(value)
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(format_value(item) for item in value) + "]"
    raise ConfigWriteError(f"cannot write TOML value of type {type(value).__name__}: {value!r}")


def _quote(text: str) -> str:
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    for character, replacement in _CONTROL_CHARS.items():
        escaped = escaped.replace(character, replacement)
    return f'"{escaped}"'


def _split_dotted(text: str) -> tuple[str, ...]:
    """Split ``a.b."c d"`` into ``("a", "b", "c d")``."""
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for character in text:
        if quote is not None:
            if character == quote:
                quote = None
            else:
                current.append(character)
        elif character in "\"'":
            quote = character
        elif character == ".":
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    parts.append("".join(current).strip())
    return tuple(part for part in parts if part)


def _format_header(parts: Sequence[str]) -> str:
    rendered = []
    for part in parts:
        rendered.append(part if _BARE_KEY_RE.match(part) else _quote(part))
    return "[" + ".".join(rendered) + "]"


class TomlDocument:
    """A line based TOML document that can be patched in place."""

    def __init__(self, lines: Iterable[str]) -> None:
        self._lines: list[str] = list(lines)

    # -- construction ---------------------------------------------------
    @classmethod
    def from_text(cls, text: str) -> "TomlDocument":
        return cls(text.splitlines())

    @classmethod
    def from_path(cls, path: Path) -> "TomlDocument":
        try:
            raw = Path(path).read_bytes()
        except OSError as exc:
            raise ConfigWriteError(f"cannot read {path} for editing: {exc}") from exc
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ConfigWriteError(f"{path} is not valid UTF-8: {exc}") from exc
        return cls.from_text(text)

    def text(self) -> str:
        return "\n".join(self._lines) + "\n"

    def write(self, path: Path) -> bool:
        """Atomically save the document.  Returns True when something changed."""
        path = Path(path)
        new_text = self.text()
        try:
            old_text = path.read_bytes().decode("utf-8-sig")
        except (OSError, UnicodeDecodeError):
            old_text = None
        if old_text is not None and old_text.replace("\r\n", "\n") == new_text:
            return False

        directory = path.parent
        try:
            directory.mkdir(parents=True, exist_ok=True)
            handle, temp_name = tempfile.mkstemp(
                dir=str(directory), prefix=f"{path.name}.", suffix=".tmp"
            )
        except OSError as exc:
            raise ConfigWriteError(f"cannot create a temporary file next to {path}: {exc}") from exc
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(new_text)
            os.replace(temp_name, path)
        except OSError as exc:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise ConfigWriteError(f"cannot write {path}: {exc}") from exc
        return True

    # -- public editing API ---------------------------------------------
    def has_section(self, section: Sequence[str]) -> bool:
        return self._section_bounds(section) is not None

    def set_value(self, section: Sequence[str], key: str, value: Any) -> bool:
        """Set one scalar/array value, creating the key and section as needed."""
        return self._set_assignment(section, key, [f"{key} = {format_value(value)}"])

    def set_values(self, section: Sequence[str], values: Mapping[str, Any]) -> bool:
        changed = False
        for key, value in values.items():
            changed = self.set_value(section, key, value) or changed
        return changed

    def set_values_if_missing(
        self, section: Sequence[str], values: Mapping[str, Any]
    ) -> bool:
        """Add keys without overwriting values the user already set."""
        changed = False
        for key, value in values.items():
            if self._find_key(section, key) is None:
                changed = self.set_value(section, key, value) or changed
        return changed

    def set_inline_table_array(
        self,
        section: Sequence[str],
        key: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        empty_comment: str | None = None,
    ) -> bool:
        """Replace ``key`` with a multi-line array of inline tables."""
        return self._set_assignment(section, key, _format_inline_table_array(key, rows, empty_comment))

    def create_section(
        self, section: Sequence[str], values: Mapping[str, Any] | None = None
    ) -> bool:
        """Append a brand new section at the end of the document."""
        if self.has_section(section):
            return False
        lines: list[str] = []
        if self._lines and self._lines[-1].strip():
            lines.append("")
        lines.append(_format_header(section))
        for key, value in (values or {}).items():
            lines.append(f"{key} = {format_value(value)}")
        self._lines.extend(lines)
        return True

    def remove_section(self, section: Sequence[str]) -> bool:
        """Delete a whole section (header and its assignments)."""
        bounds = self._section_bounds(section)
        if bounds is None:
            return False
        header, end = bounds
        del self._lines[header:end]
        return True

    def remove_key(self, section: Sequence[str], key: str) -> bool:
        span = self._find_key(section, key)
        if span is None:
            return False
        start, end = span
        del self._lines[start:end]
        return True

    def section_names(self) -> list[str]:
        """Dotted names of every section header, in file order."""
        names: list[str] = []
        for _index, line in self._top_level_lines():
            match = _HEADER_RE.match(line)
            if match is not None:
                names.append(".".join(_split_dotted(match.group("path"))))
        return names

    # -- internals -------------------------------------------------------
    def _top_level_lines(self) -> list[tuple[int, str]]:
        state = _ScanState()
        top: list[tuple[int, str]] = []
        for index, line in enumerate(self._lines):
            if state.at_top_level():
                top.append((index, line))
            state.scan(line)
        return top

    def _section_bounds(self, section: Sequence[str]) -> tuple[int, int] | None:
        target = tuple(section)
        top_level = self._top_level_lines()
        header_index: int | None = None
        for index, line in top_level:
            match = _HEADER_RE.match(line)
            if match is None:
                continue
            if _split_dotted(match.group("path")) == target:
                header_index = index
                break
        if header_index is None:
            return None
        end = len(self._lines)
        for index, line in top_level:
            if index > header_index and _HEADER_RE.match(line):
                end = index
                break
        return header_index, end

    def _find_key(
        self, section: Sequence[str], key: str
    ) -> tuple[int, int] | None:
        bounds = self._section_bounds(section)
        if bounds is None:
            return None
        header, end = bounds
        state = _ScanState()
        index = header + 1
        while index < end:
            line = self._lines[index]
            if state.at_top_level():
                match = _ASSIGNMENT_RE.match(line)
                if match is not None and _unquote_key(match.group("key")) == key:
                    start = index
                    value_state = _ScanState()
                    value_state.scan(match.group("value"))
                    while not value_state.at_top_level() and index + 1 < end:
                        index += 1
                        value_state.scan(self._lines[index])
                    return start, index + 1
            state.scan(line)
            index += 1
        return None

    def _set_assignment(
        self, section: Sequence[str], key: str, block: Sequence[str]
    ) -> bool:
        block = list(block)
        span = self._find_key(section, key)
        if span is not None:
            start, end = span
            if self._lines[start:end] == block:
                return False
            self._lines[start:end] = block
            return True

        bounds = self._section_bounds(section)
        if bounds is None:
            if self._lines and self._lines[-1].strip():
                self._lines.append("")
            self._lines.append(_format_header(section))
            self._lines.extend(block)
            return True

        header, end = bounds
        insert_at = header + 1
        for index in range(header + 1, end):
            if self._lines[index].strip():
                insert_at = index + 1
        self._lines[insert_at:insert_at] = block
        return True


def _unquote_key(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _format_inline_table_array(
    key: str,
    rows: Sequence[Mapping[str, Any]],
    empty_comment: str | None = None,
) -> list[str]:
    if not rows:
        if empty_comment:
            return [f"{key} = [", f"  # {empty_comment}", "]"]
        return [f"{key} = []"]
    lines = [f"{key} = ["]
    for row in rows:
        body = ", ".join(f"{name} = {format_value(value)}" for name, value in row.items())
        lines.append(f"  {{ {body} }},")
    lines.append("]")
    return lines
