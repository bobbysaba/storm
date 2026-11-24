"""Unknown keys in a settings table: an error, or with *warn* a warning and
the key left out, each with a "did you mean" for a near miss. Shared by
storm.toml (settings/, data/sources.py, data/auth.py) and the headless
publisher's own file."""
from __future__ import annotations

import difflib
from typing import Callable, Iterable

Warn = Callable[[str], None]


def did_you_mean(name: str, known: Iterable[str]) -> str:
    """' (did you mean password?)' for a near miss of *name*, else ''."""
    close = difflib.get_close_matches(name, sorted(known), n=1, cutoff=0.75)
    return f" (did you mean {close[0]}?)" if close else ""


def unknown_message(where: str, unknown: Iterable[str], known: Iterable[str], what: str = "key") -> str:
    """'[map]: unknown key centre (did you mean center?)', one clause per key."""
    known = tuple(known)
    parts = [name + did_you_mean(name, known) for name in sorted(unknown)]
    return f"{where}: unknown {what}{'s' if len(parts) > 1 else ''} {', '.join(parts)}"


def check_keys(table: dict, known: Iterable[str], where: str, error: type[Exception],
               warn: Warn | None = None, what: str = "key", skip: Callable[[str, object], bool] | None = None) -> dict:
    """*table* without the keys outside *known*. Without *warn* an unknown
    key raises *error*; with it, *warn* gets the message and the key is left
    out. *skip(key, value)* marks keys checked elsewhere (sub-tables)."""
    known = frozenset(known)
    unknown = {k for k, v in table.items() if k not in known and not (skip and skip(k, v))}
    if not unknown:
        return table
    message = unknown_message(where, unknown, known, what)
    if warn is None:
        raise error(message)
    warn(message + " (left out)")
    return {k: v for k, v in table.items() if k not in unknown}
