"""Regression coverage for trustworthy per-home disk measurements."""

from __future__ import annotations

import app


def test_parse_home_usage_marks_unreadable_homes_unavailable() -> None:
    payload = """---HOME---
38869454029\t/home/gorbov_gv
---ALL_HOMES---
gorbov_gv\t38869454029\t/home/gorbov_gv
other_user\t\t/home/other_user\tunavailable
---ALL_HOMES_STATUS---
partial\t1\t24
"""

    result = app._parse_output("aicenter3", payload, "", 0)

    assert result["all_homes"] == [{
        "username": "gorbov_gv", "used_bytes": 38869454029,
        "path": "/home/gorbov_gv", "available": True,
    }, {
        "username": "other_user", "used_bytes": None,
        "path": "/home/other_user", "available": False,
    }]
    assert result["all_homes_status"] == {
        "state": "partial", "measured": 1, "total": 24,
    }


def test_parse_home_usage_accepts_complete_zero_byte_home() -> None:
    payload = """---ALL_HOMES---
new_user\t0\t/home/new_user
---ALL_HOMES_STATUS---
complete\t1\t1
"""

    result = app._parse_output("aicenter3", payload, "", 0)

    assert result["all_homes"][0]["used_bytes"] == 0
    assert result["all_homes_status"]["state"] == "complete"
