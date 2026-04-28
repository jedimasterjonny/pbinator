from pbinator.settings import Settings


def test_settings_class_can_be_instantiated() -> None:
    s = Settings()
    assert isinstance(s, Settings)
