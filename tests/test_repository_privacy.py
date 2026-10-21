"""Prevent accidental inclusion of user files in the shareable repository."""
import re
import subprocess
from pathlib import Path


def test_tracked_files_exclude_user_state_and_credentials():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['git','-c','safe.directory=*','ls-files','-z'], cwd=root, check=True, capture_output=True)
    files = result.stdout.decode().split('\0')
    for name in filter(None, files):
        path = Path(name)
        assert path.parts[0] not in {'.local', 'knowledge', 'sources', 'artifacts', '.env', '.venv'}
        assert path.suffix.lower() not in {'.pdf', '.docx', '.xlsx', '.sqlite3', '.db'}
        if path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.webp'}:
            continue
        text = (root / name).read_text(encoding='utf-8')
        assert not re.search(r'sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}', text), name
        assert not re.search(r'gh[pousr]_[A-Za-z0-9]{20,}', text), name
        assert not re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', text), name
