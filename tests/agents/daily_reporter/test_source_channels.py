from src.agents.daily_reporter import config as dr_config
from src.agents.daily_reporter import logic


def test_parse_id_list():
    assert dr_config._parse_id_list("") == []
    assert dr_config._parse_id_list("  ,  ") == []
    assert dr_config._parse_id_list("123, 456") == [123, 456]
    assert dr_config._parse_id_list("123,abc, 456 ") == [123, 456]


def test_resolve_source_channels_expands_categories_and_excludes(monkeypatch):
    class DummyTextChannel:
        def __init__(self, channel_id):
            self.id = channel_id

    class DummyForumChannel:
        def __init__(self, channel_id):
            self.id = channel_id

    class DummyCategoryChannel:
        def __init__(self, channel_id, channels):
            self.id = channel_id
            self.channels = channels

    class DummyClient:
        def __init__(self, mapping):
            self._mapping = mapping

        def get_channel(self, channel_id):
            return self._mapping.get(channel_id)

    monkeypatch.setattr(logic.discord, "TextChannel", DummyTextChannel)
    monkeypatch.setattr(logic.discord, "ForumChannel", DummyForumChannel)
    monkeypatch.setattr(logic.discord, "CategoryChannel", DummyCategoryChannel)

    text1 = DummyTextChannel(101)
    text2 = DummyTextChannel(102)
    forum1 = DummyForumChannel(103)
    text3 = DummyTextChannel(104)
    explicit = DummyTextChannel(200)

    cat1 = DummyCategoryChannel(1000, [text1, text2, forum1])
    cat2 = DummyCategoryChannel(1001, [text3])

    client = DummyClient({
        1000: cat1,
        1001: cat2,
        200: explicit,
        101: text1,
        102: text2,
        103: forum1,
        104: text3,
    })

    monkeypatch.setattr(logic.config, "SOURCE_CHANNELS", [200])
    monkeypatch.setattr(logic.config, "SOURCE_CATEGORY_IDS", [1000, 1001])
    monkeypatch.setattr(logic.config, "SOURCE_CHANNEL_EXCLUDE_IDS", {102})

    agent = logic.DailyReporterAgent()
    agent.client = client

    resolved = agent.resolve_source_channels()
    resolved_ids = [channel.id for channel in resolved]

    assert resolved_ids == [101, 103, 104, 200]


def test_resolve_source_channels_skips_missing_category(monkeypatch):
    class DummyTextChannel:
        def __init__(self, channel_id):
            self.id = channel_id

    class DummyCategoryChannel:
        def __init__(self, channel_id, channels):
            self.id = channel_id
            self.channels = channels

    class DummyClient:
        def __init__(self, mapping):
            self._mapping = mapping

        def get_channel(self, channel_id):
            return self._mapping.get(channel_id)

    monkeypatch.setattr(logic.discord, "TextChannel", DummyTextChannel)
    monkeypatch.setattr(logic.discord, "ForumChannel", DummyTextChannel)
    monkeypatch.setattr(logic.discord, "CategoryChannel", DummyCategoryChannel)

    text1 = DummyTextChannel(301)
    cat1 = DummyCategoryChannel(2000, [text1])
    not_category = DummyTextChannel(2001)

    client = DummyClient({
        2000: cat1,
        2001: not_category,
        301: text1,
    })

    monkeypatch.setattr(logic.config, "SOURCE_CHANNELS", [])
    monkeypatch.setattr(logic.config, "SOURCE_CATEGORY_IDS", [2000, 2001, 9999])
    monkeypatch.setattr(logic.config, "SOURCE_CHANNEL_EXCLUDE_IDS", set())

    agent = logic.DailyReporterAgent()
    agent.client = client

    resolved = agent.resolve_source_channels()
    resolved_ids = [channel.id for channel in resolved]

    assert resolved_ids == [301]
