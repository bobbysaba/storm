from core import mem_probe


def test_memory_figures_are_numbers_and_never_raise():
    peak = mem_probe.peak_rss_mb()
    current = mem_probe.current_rss_mb()
    total, available = mem_probe.system_memory_mb()
    assert peak > 0 and current > 0 and total > 0
    assert available == -1.0 or 0 < available <= total
    mem_probe.log_delta("test", peak, peak, 1.0)          # logs, never raises
    mem_probe.log_delta("test", -1.0, peak, 1.0)
