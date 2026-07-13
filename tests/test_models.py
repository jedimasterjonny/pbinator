from pbinator import models


def test_table_names_are_stable() -> None:
    # Renaming a table silently orphans an existing data/pbinator.db.
    assert models.Activity.__tablename__ == "activity"
    assert models.BestEffort.__tablename__ == "best_effort"
    assert models.SyncCursor.__tablename__ == "sync_cursor"
