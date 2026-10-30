import sqlite3
import subprocess

import pytest

from sebastian.backup import DUMP_NAME, OffsiteError, push_offsite


def git(*args, cwd):
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout


@pytest.fixture
def clone(tmp_path):
    origin = tmp_path / "origin.git"
    git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    work = tmp_path / "offsite"
    git("clone", "-q", str(origin), str(work), cwd=tmp_path)
    git("config", "user.email", "t@example.com", cwd=work)
    git("config", "user.name", "t", cwd=work)
    git("checkout", "-q", "-b", "main", cwd=work)
    return origin, work


def make_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("create table t (id integer primary key, title text)")
    con.executemany("insert into t(title) values (?)", [(r,) for r in rows])
    con.commit()
    con.close()
    return path


def test_pushes_a_dump_only_when_something_changed(tmp_path, clone):
    origin, work = clone
    db = make_db(tmp_path / "sebastian-20261030T033000Z.db", ["pay rent", 'it\'s "quoted"'])
    assert push_offsite(db, work) == "offsite: pushed"
    assert push_offsite(db, work) == "offsite: no changes since the last backup"
    assert git("rev-list", "--count", "main", cwd=origin).strip() == "1"
    assert "backup 20261030T033000Z" in git("log", "-1", "--format=%s", cwd=origin)

    con = sqlite3.connect(db)
    con.execute("insert into t(title) values ('new task')")
    con.commit()
    con.close()
    assert push_offsite(db, work) == "offsite: pushed"
    assert git("rev-list", "--count", "main", cwd=origin).strip() == "2"


def test_dump_restores_into_an_identical_database(tmp_path, clone):
    origin, work = clone
    db = make_db(tmp_path / "sebastian-20261030T033000Z.db", ["pay rent", 'it\'s "quoted"'])
    push_offsite(db, work)
    restored = sqlite3.connect(tmp_path / "restored.db")
    restored.executescript(git("show", f"main:{DUMP_NAME}", cwd=origin))
    assert restored.execute("select title from t order by id").fetchall() == [
        ("pay rent",),
        ('it\'s "quoted"',),
    ]


def test_skipped_when_not_configured(tmp_path):
    db = make_db(tmp_path / "sebastian-x.db", ["a"])
    assert "not configured" in push_offsite(db, tmp_path / "nope")


def test_push_failure_is_reported(tmp_path, clone):
    origin, work = clone
    db = make_db(tmp_path / "sebastian-x.db", ["a"])
    git("remote", "set-url", "origin", str(tmp_path / "gone.git"), cwd=work)
    with pytest.raises(OffsiteError, match="push"):
        push_offsite(db, work)
