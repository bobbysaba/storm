"""Command palette matching, providers and recents (no Qt)."""
from ui.command_registry import MAX_RECENT, Command, CommandRegistry, command_score, score


def _cmd(cid, title, category="Action", **kw):
    return Command(cid, title, category, lambda: None, **kw)


def test_score_needs_every_char_in_order():
    assert score("rdr", "Radar") is not None
    assert score("rrd", "Radar") is None
    assert score("radars", "Radar") is None
    assert score("", "Radar") == 0.0


def test_spaces_and_case_are_ignored():
    assert score("ktlx okl", "KTLX Oklahoma City") is not None
    assert score("KTLX", "ktlx") == score("ktlx", "ktlx")


def test_prefix_beats_word_start_beats_scattered():
    prefix = score("sat", "Satellite CONUS")
    word = score("sat", "Show satellite")
    scattered = score("sat", "Scan pattern")
    assert prefix > word > scattered


def test_word_starts_beat_mid_word_letters():
    # "ow" at word starts in "Open Warnings" vs inside "Show"
    assert score("ow", "Open Warnings") > score("ow", "Shows")


def test_consecutive_letters_beat_spread_ones():
    assert score("meso", "MESO-1") > score("meso", "Measure some outlook")


def test_category_and_keywords_match_with_a_penalty():
    cmd = _cmd("site:KTLX", "KTLX Oklahoma City", "Radar site", keywords="okc")
    assert command_score("radar ktlx", cmd) is not None
    assert command_score("okc", cmd) is not None
    other = _cmd("x", "Radar ktlx", "Action")
    # the same text in the title outranks it in the category
    assert command_score("radar ktlx", other) > command_score("radar ktlx", cmd)


def test_providers_are_called_on_refresh_and_failures_skipped():
    calls = []
    reg = CommandRegistry()

    def good():
        calls.append("good")
        return [_cmd("a", "Alpha"), _cmd("b", "Beta")]

    def bad():
        raise RuntimeError("boom")

    reg.add_provider("good", good)
    reg.add_provider("bad", bad)
    reg.add_provider("dupe", lambda: [_cmd("a", "Alpha again")])
    assert reg.provider_names() == ["good", "bad", "dupe"]
    assert reg.commands == []
    reg.refresh()
    assert [c.title for c in reg.commands] == ["Alpha", "Beta"]
    reg.search("al")
    reg.search("be")
    assert calls == ["good"]          # search ranks the snapshot; no provider calls


def test_search_ranks_and_limits():
    reg = CommandRegistry()
    reg.add_provider("p", lambda: [
        _cmd("1", "Show satellite"), _cmd("2", "Satellite CONUS"), _cmd("3", "Radar"),
    ])
    reg.refresh()
    assert [c.id for c in reg.search("sat")] == ["2", "1"]
    assert len(reg.search("", limit=2)) == 2


def test_recent_commands_come_first_for_an_empty_query():
    saved = []
    reg = CommandRegistry(load_recent=lambda: ["3"], save_recent=saved.append)
    reg.add_provider("p", lambda: [_cmd(str(i), f"Command {i}") for i in range(5)])
    reg.refresh()
    assert [c.id for c in reg.search("")][:2] == ["3", "0"]
    reg.run(reg.commands[1])
    assert saved[-1] == ["1", "3"]
    assert [c.id for c in reg.search("")][:3] == ["1", "3", "0"]


def test_recent_list_is_capped_and_deduplicated():
    reg = CommandRegistry()
    for i in range(MAX_RECENT + 5):
        reg.record_use(str(i))
    reg.record_use("3")
    assert len(reg.recent) == MAX_RECENT
    assert reg.recent[0] == "3" and reg.recent.count("3") == 1


def test_recent_boost_breaks_a_tie_in_a_query():
    reg = CommandRegistry()
    reg.add_provider("p", lambda: [_cmd("a", "Radar velocity"), _cmd("b", "Radar reflectivity")])
    reg.refresh()
    assert reg.search("radar")[0].id == "a"
    reg.record_use("b")
    assert reg.search("radar")[0].id == "b"


def test_missing_recents_are_skipped_and_bad_storage_is_tolerated():
    def broken():
        raise OSError("no settings")

    reg = CommandRegistry(load_recent=broken)
    assert reg.recent == []
    reg = CommandRegistry(load_recent=lambda: ["gone", "a"])
    reg.add_provider("p", lambda: [_cmd("a", "Alpha"), _cmd("b", "Beta")])
    reg.refresh()
    assert [c.id for c in reg.search("")] == ["a", "b"]


def test_extra_commands_join_the_search():
    reg = CommandRegistry()
    reg.add_provider("p", lambda: [_cmd("a", "Alpha")])
    reg.refresh()
    place = _cmd("place:1", "Norman, Oklahoma", "Place", keywords="norman")
    assert [c.id for c in reg.search("norman", extra=[place])] == ["place:1"]
