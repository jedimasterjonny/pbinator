from pbinator.settings import Settings, settings


def test_settings_instance() -> None:
    assert isinstance(settings, Settings)
