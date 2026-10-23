import json

from app.core.platforms.bilibili_login import BilibiliLoginSession


def test_save_biliup_credentials_and_rotate_backup(tmp_path):
    path = tmp_path / "bilibili" / "cookies.json"
    session = BilibiliLoginSession(str(path))
    first = {"cookie_info": {"cookies": [{"name": "SESSDATA", "value": "one"}]}, "token_info": {}}
    session.save(first)
    data = json.loads(path.read_text())
    assert data["cookie_info"]["cookies"][0] == {"name": "SESSDATA", "value": "one"}
    assert path.stat().st_mode & 0o777 == 0o600
    assert session.snapshot()["state"] == "confirmed"
    session2 = BilibiliLoginSession(str(path))
    second = {"cookie_info": {"cookies": [{"name": "SESSDATA", "value": "two"}]}, "token_info": {}}
    session2.save(second)
    backups = list(path.parent.glob("cookies-*.bak"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text())["cookie_info"]["cookies"][0]["value"] == "one"


def test_cancel_prevents_late_cookie_write(tmp_path):
    path = tmp_path / "cookies.json"
    session = BilibiliLoginSession(str(path))
    session.cancel()
    session.save({"cookie_info": {"cookies": []}, "token_info": {}})
    assert not path.exists()
    assert session.snapshot()["state"] == "cancelled"
