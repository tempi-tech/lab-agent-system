from src.core.discord_access import DiscordAccessPolicy, is_message_allowed


class DummyChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id


class DummyMessage:
    def __init__(self, channel_id: int, mentions) -> None:
        self.channel = DummyChannel(channel_id)
        self.mentions = mentions


class DummyUser:
    pass


def test_is_message_allowed_disabled_policy():
    policy = DiscordAccessPolicy(enabled=False, allowed_channel_ids=[123], require_mention=True)
    message = DummyMessage(channel_id=999, mentions=[])
    assert is_message_allowed(message, policy, None) is True


def test_is_message_allowed_channel_allowlist():
    policy = DiscordAccessPolicy(enabled=True, allowed_channel_ids=[123], require_mention=False)
    message = DummyMessage(channel_id=999, mentions=[])
    assert is_message_allowed(message, policy, None) is False


def test_is_message_allowed_requires_mention():
    policy = DiscordAccessPolicy(enabled=True, allowed_channel_ids=[], require_mention=True)
    bot = DummyUser()
    message = DummyMessage(channel_id=123, mentions=[])
    assert is_message_allowed(message, policy, bot) is False

    message = DummyMessage(channel_id=123, mentions=[bot])
    assert is_message_allowed(message, policy, bot) is True
