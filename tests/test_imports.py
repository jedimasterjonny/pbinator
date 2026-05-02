import pbinator
from pbinator import models


def test_package_imports() -> None:
    assert pbinator is not None


def test_models_imports() -> None:
    assert models.Activity.__tablename__ == "activity"
    assert models.BestEffort.__tablename__ == "best_effort"
    assert models.SyncCursor.__tablename__ == "sync_cursor"
