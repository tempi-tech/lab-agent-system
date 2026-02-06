from src.agents.membership_checker.logic import format_status_delta


def test_format_status_delta_changes_only():
    prev = {
        "members": {
            "in_server_with_role": [1, 2],
            "in_server_without_role": [3],
            "not_in_server": [],
            "username_in_server": [],
            "username_not_in_server": [4, 5],
        }
    }
    cur = {
        "members": {
            "in_server_with_role": [1, 2, 9],
            "in_server_without_role": [],
            "not_in_server": [],
            "username_in_server": [],
            "username_not_in_server": [4, 5],
        }
    }

    text = format_status_delta(prev, cur)
    assert "in_with_role: 2 -> 3 (Δ +1)" in text
    assert "in_without_role: 1 -> 0 (Δ -1)" in text
    # unchanged lines should be omitted
    assert "username_not_in_server" not in text


def test_format_status_delta_no_change():
    prev = {"members": {"in_server_with_role": [1]}}
    cur = {"members": {"in_server_with_role": [1]}}
    text = format_status_delta(prev, cur)
    assert "変化なし" in text
