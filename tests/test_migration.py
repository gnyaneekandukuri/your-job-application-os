import sqlite3
import pytest
from scripts.migrate_workspace import migrate


def test_migration_preserves_data_and_refuses_overwrite(tmp_path):
    source, dest = tmp_path/'old', tmp_path/'new'
    (source/'.local/living-stories').mkdir(parents=True)
    (source/'knowledge').mkdir()
    (source/'knowledge/profile.json').write_text('{"name":"Fictional Candidate"}')
    (source/'.local/living-stories/story.md').write_text('Fictional story')
    with sqlite3.connect(source/'.local/reviews.sqlite3') as db:
        db.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY, payload TEXT)')
        db.execute('INSERT INTO jobs VALUES (?,?)', ('example','saved history'))
    result = migrate(source, dest)
    assert result['databases']['reviews.sqlite3']['jobs'] == 1
    assert result['verified_files'] == 2
    assert (dest/'workspace/knowledge/profile.json').read_bytes() == (source/'knowledge/profile.json').read_bytes()
    with sqlite3.connect(dest/'reviews.sqlite3') as db:
        assert db.execute('SELECT payload FROM jobs').fetchone()[0] == 'saved history'
    with pytest.raises(ValueError):
        migrate(source, dest)
    assert (source/'.local/living-stories/story.md').read_text() == 'Fictional story'


def test_active_workspace_selection_fails_closed(tmp_path, monkeypatch):
    import app.user_workspace as workspace
    monkeypatch.setattr(workspace, 'PRODUCT', tmp_path)
    monkeypatch.delenv('JOB_REVIEW_DATA_DIR', raising=False)
    local = tmp_path/'.local'
    local.mkdir()
    marker = local/'active-workspace'
    marker.write_text('../outside')
    with pytest.raises(ValueError):
        workspace.default_data_directory()
    marker.write_text('personal')
    with pytest.raises(ValueError):
        workspace.default_data_directory()
    (local/'personal').mkdir()
    (local/'personal/reviews.sqlite3').touch()
    assert workspace.default_data_directory() == (local/'personal').resolve()
