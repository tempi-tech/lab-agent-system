from src.agents.question_sla.logic import _parse_starter_message_id


def test_parse_starter_message_id_digits():
    assert _parse_starter_message_id("123") == 123


def test_parse_starter_message_id_jump_url():
    assert (
        _parse_starter_message_id("https://discord.com/channels/1/2/999")
        == 999
    )


def test_parse_starter_message_id_invalid():
    assert _parse_starter_message_id("nope") is None

