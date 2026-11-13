"""Everything the command palette can run, and how a query ranks it.

Providers are callables that return the ``Command``s available right now (in
this launch mode, with this data loaded). ``refresh`` calls them once when the
palette opens and ``search`` ranks that snapshot on every keystroke, so a
provider can afford some work (distances to radar sites, say).

Matching is a subsequence score: every query character must appear in order.
Matches at the start of the title or of a word, and runs of consecutive
characters, score higher. The last ``MAX_RECENT`` commands run are listed first
for an empty query and get a boost otherwise.

No Qt here: recents are loaded and saved through injected callables.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable

log = logging.getLogger(__name__)

MAX_RECENT = 10

_PREFIX_BONUS = 10.0
_FIRST_CHAR_BONUS = 4.0
_WORD_START_BONUS = 3.0
_CONSECUTIVE_BONUS = 3.5     # above the word-start bonus, so a run beats a jump
_GAP_PENALTY = 0.15
_CATEGORY_PENALTY = 2.0
_RECENT_BONUS = 6.0
_WORD_SEPARATORS = " -_/()·.,:"


@dataclass(frozen=True)
class Command:
    id: str                      # stable key, used for recents: "action:radar", "site:KTLX"
    title: str
    category: str
    run: Callable[[], None]
    keys: str = ""               # shortcut as shown to the user
    hint: str = ""               # secondary text: distance, current state
    keywords: str = ""           # extra text to match, not shown


def _is_word_start(text: str, i: int) -> bool:
    if i == 0:
        return True
    prev, cur = text[i - 1], text[i]
    if prev in _WORD_SEPARATORS:
        return True
    return prev.islower() and cur.isupper()      # camelCase


def _is_subsequence(q: str, text: str) -> bool:
    pos = 0
    for ch in q:
        pos = text.find(ch, pos)
        if pos < 0:
            return False
        pos += 1
    return True


def score(query: str, text: str) -> float | None:
    """How well ``query`` matches ``text``; None when it doesn't.

    Spaces in the query are ignored, so "ktlx okc" matches "KTLX Oklahoma City".
    """
    q = "".join(query.lower().split())
    if not q:
        return 0.0
    lower = text.lower()
    n, m = len(q), len(lower)
    if n > m:
        return None

    if not _is_subsequence(q, lower):
        return None

    neg = float("-inf")
    # best[j]: best score with the current query char matched at text[j]
    best = [neg] * m
    for j in range(m):
        if lower[j] == q[0]:
            if j == 0:
                bonus = _FIRST_CHAR_BONUS
            else:
                bonus = _WORD_START_BONUS if _is_word_start(text, j) else 0.0
            best[j] = 1.0 + bonus - _GAP_PENALTY * j
    for i in range(1, n):
        nxt = [neg] * m
        carry = neg             # max over k < j of best[k] less a penalty per skipped char
        for j in range(1, m):
            carry = max(carry - _GAP_PENALTY, best[j - 1])
            if lower[j] != q[i] or carry == neg:
                continue
            here = 1.0 + (_WORD_START_BONUS if _is_word_start(text, j) else 0.0)
            nxt[j] = max(best[j - 1] + _CONSECUTIVE_BONUS, carry) + here
        best = nxt
    top = max(best)
    if top == neg:
        return None
    if lower.startswith(q):
        top += _PREFIX_BONUS
    return top - 0.01 * m


def command_score(query: str, cmd: Command) -> float | None:
    """The best of the title, the title with its category, and the keywords."""
    scores = [score(query, cmd.title)]
    with_category = score(query, f"{cmd.category} {cmd.title}")
    if with_category is not None:
        scores.append(with_category - _CATEGORY_PENALTY)
    if cmd.keywords:
        kw = score(query, cmd.keywords)
        if kw is not None:
            scores.append(kw - _CATEGORY_PENALTY)
    found = [s for s in scores if s is not None]
    return max(found) if found else None


class CommandRegistry:
    def __init__(self, load_recent: Callable[[], list[str]] | None = None,
                 save_recent: Callable[[list[str]], None] | None = None):
        self._providers: list[tuple[str, Callable[[], Iterable[Command]]]] = []
        self._snapshot: list[Command] = []
        self._save_recent = save_recent
        recent = []
        if load_recent is not None:
            try:
                recent = [str(r) for r in (load_recent() or [])]
            except Exception:
                log.exception("could not load recent palette commands")
        self._recent: list[str] = recent[:MAX_RECENT]

    # ── providers ─────────────────────────────────────────
    def add_provider(self, name: str, provider: Callable[[], Iterable[Command]]) -> None:
        self._providers.append((name, provider))

    def provider_names(self) -> list[str]:
        return [name for name, _ in self._providers]

    def refresh(self) -> list[Command]:
        """Ask every provider for its commands now; a failing provider is skipped."""
        seen: set[str] = set()
        commands: list[Command] = []
        for name, provider in self._providers:
            try:
                provided = list(provider())
            except Exception:
                log.exception("command palette provider %r failed", name)
                continue
            for cmd in provided:
                if cmd.id in seen:
                    continue
                seen.add(cmd.id)
                commands.append(cmd)
        self._snapshot = commands
        return commands

    @property
    def commands(self) -> list[Command]:
        return list(self._snapshot)

    # ── recents ───────────────────────────────────────────
    @property
    def recent(self) -> list[str]:
        return list(self._recent)

    def record_use(self, command_id: str) -> None:
        self._recent = [command_id] + [r for r in self._recent if r != command_id]
        del self._recent[MAX_RECENT:]
        if self._save_recent is not None:
            try:
                self._save_recent(list(self._recent))
            except Exception:
                log.exception("could not save recent palette commands")

    def run(self, cmd: Command) -> None:
        self.record_use(cmd.id)
        cmd.run()

    # ── search ────────────────────────────────────────────
    def search(self, query: str, limit: int = 50,
               extra: Iterable[Command] = ()) -> list[Command]:
        """The snapshot (plus ``extra``, e.g. place results) ranked for ``query``."""
        pool = self._snapshot + [c for c in extra]
        rank = {cid: i for i, cid in enumerate(self._recent)}
        if not query.strip():
            recent = sorted((c for c in pool if c.id in rank), key=lambda c: rank[c.id])
            rest = [c for c in pool if c.id not in rank]
            return (recent + rest)[:limit]

        scored = []
        for order, cmd in enumerate(pool):
            s = command_score(query, cmd)
            if s is None:
                continue
            if cmd.id in rank:
                s += _RECENT_BONUS * (1 - rank[cmd.id] / MAX_RECENT)
            scored.append((-s, order, cmd))
        scored.sort(key=lambda t: (t[0], t[1]))
        return [cmd for _, _, cmd in scored[:limit]]
