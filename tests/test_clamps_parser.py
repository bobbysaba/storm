"""Tests for the CLAMPS .skewT parser."""

import math
from datetime import datetime, timezone

from data.fetchers.clamps_sounding_fetcher import _parse_skewt, _FILENAME_RE, _format_label


# minimal valid .skewT content
SAMPLE_SKEWT = """\
%TITLE%
 NSSL_LIDAR 260406/1800
%RAW%
  1013.0,  350.0,  25.0,  18.0,  180.0,  5.0
  1000.0,  400.0,  24.0,  17.0,  185.0,  6.0
   950.0,  550.0,  20.0,  14.0,  200.0,  8.0
   900.0,  700.0,  16.0,  10.0,  210.0, 10.0
   850.0,  900.0,  12.0,   6.0,  220.0, 12.0
   800.0, 1100.0,   8.0,   2.0,  230.0, 14.0
   700.0, 1500.0,   0.0,  -5.0,  250.0, 18.0
%END%
"""


class TestParseSkewt:
    def test_basic_parse(self):
        ft = datetime(2026, 4, 6, 18, 0, tzinfo=timezone.utc)
        snd = _parse_skewt(SAMPLE_SKEWT, ft, 0)
        assert snd is not None
        assert len(snd.pressure) == 7
        assert snd.pressure[0] == 1013.0
        assert snd.pressure[-1] == 700.0
        assert snd.temperature[0] == 25.0
        assert snd.dewpoint[0] == 18.0
        assert snd.height[0] == 350.0

    def test_wind_components(self):
        ft = datetime(2026, 4, 6, 18, 0, tzinfo=timezone.utc)
        snd = _parse_skewt(SAMPLE_SKEWT, ft, 0)
        # first row: wdir=180, wspd=5 kt → convert to m/s before components.
        expected = 5.0 / 1.944
        assert abs(snd.u_wind[0]) < 1e-10
        assert abs(snd.v_wind[0] - expected) < 1e-10

    def test_too_few_levels_returns_none(self):
        short = """\
%TITLE%
 NSSL_LIDAR 260406/1800
%RAW%
  1013.0,  350.0,  25.0,  18.0,  180.0,  5.0
  1000.0,  400.0,  24.0,  17.0,  185.0,  6.0
%END%
"""
        ft = datetime(2026, 4, 6, 18, 0, tzinfo=timezone.utc)
        assert _parse_skewt(short, ft, 0) is None

    def test_empty_raw_section(self):
        empty = """\
%TITLE%
 NSSL_LIDAR 260406/1800
%RAW%
%END%
"""
        ft = datetime(2026, 4, 6, 18, 0, tzinfo=timezone.utc)
        assert _parse_skewt(empty, ft, 0) is None

    def test_bad_lines_skipped(self):
        mixed = """\
%TITLE%
 NSSL_LIDAR 260406/1800
%RAW%
  1013.0,  350.0,  25.0,  18.0,  180.0,  5.0
  not,valid,data
  1000.0,  400.0,  24.0,  17.0,  185.0,  6.0
  short,row
   950.0,  550.0,  20.0,  14.0,  200.0,  8.0
   900.0,  700.0,  16.0,  10.0,  210.0, 10.0
   850.0,  900.0,  12.0,   6.0,  220.0, 12.0
%END%
"""
        ft = datetime(2026, 4, 6, 18, 0, tzinfo=timezone.utc)
        snd = _parse_skewt(mixed, ft, 0)
        assert snd is not None
        assert len(snd.pressure) == 5  # bad lines skipped


class TestFilenameRegex:
    def test_matches_standard(self):
        m = _FILENAME_RE.search("upperair.NSSL_Lidar_sonde.202506061759.skewT")
        assert m is not None
        assert m.group(1) == "202506061759"

    def test_case_insensitive(self):
        m = _FILENAME_RE.search("upperair.NSSL_Lidar_sonde.202506061759.SKEWT")
        assert m is not None

    def test_no_match(self):
        m = _FILENAME_RE.search("random_file.txt")
        assert m is None


class TestFormatLabel:
    def test_format(self):
        dt = datetime(2026, 4, 6, 18, 30, tzinfo=timezone.utc)
        assert _format_label(dt) == "1830Z Apr 6"
