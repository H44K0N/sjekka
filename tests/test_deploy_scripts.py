"""deploy/deploy.sh and deploy/backup.sh, run against a temporary directory.
deploy.sh talks to a fake docker that records its calls."""
import gzip
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SHA = "a" * 40
OLD_IMAGE = "ghcr.io/h44k0n/sjekka:" + "b" * 40
NEW_IMAGE = "ghcr.io/h44k0n/sjekka:" + SHA

FAKE_DOCKER = """#!/usr/bin/env bash
echo "SJEKKA_IMAGE=${SJEKKA_IMAGE:-} $*" >> "$FAKE_LOG"
case "$1" in
    login) cat > /dev/null ;;
    compose)
        case "$2" in
            up) echo "$SJEKKA_IMAGE" > "$FAKE_RUNNING" ;;
            ps) echo container123 ;;
        esac ;;
    inspect)
        if [ "$(cat "$FAKE_RUNNING")" = "${FAKE_UNHEALTHY:-}" ]; then echo unhealthy; else echo healthy; fi ;;
esac
"""


@pytest.fixture
def deploy_env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(FAKE_DOCKER)
    (bin_dir / "sleep").write_text("#!/bin/sh\n")  # don't wait between health checks
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    app_dir = tmp_path / "opt"
    (app_dir / "state").mkdir(parents=True)
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SJEKKA_DIR": str(app_dir),
        "FAKE_LOG": str(tmp_path / "docker.log"),
        "FAKE_RUNNING": str(tmp_path / "running"),
    }


def deploy(env, *args, **extra_env):
    return subprocess.run(
        ["bash", str(ROOT / "deploy/deploy.sh"), *args],
        input="ghcr-token", capture_output=True, text=True, env=env | extra_env,
    )


def state(env):
    path = Path(env["SJEKKA_DIR"]) / "state/deployed-image"
    return path.read_text().strip() if path.exists() else None


def docker_calls(env):
    return Path(env["FAKE_LOG"]).read_text().splitlines()


@pytest.mark.parametrize("arg", ["", "main", "a" * 39, "A" * 40, "a" * 40 + "; rm -rf /"])
def test_deploy_rejects_anything_but_a_commit_sha(deploy_env, arg):
    res = deploy(deploy_env, arg)
    assert res.returncode == 2
    assert not Path(deploy_env["FAKE_LOG"]).exists()  # docker never ran


def test_deploy_takes_sha_from_forced_ssh_command(deploy_env):
    res = deploy(deploy_env, SSH_ORIGINAL_COMMAND=SHA)
    assert res.returncode == 0, res.stderr
    assert state(deploy_env) == NEW_IMAGE


def test_successful_deploy_records_image_and_prunes(deploy_env):
    res = deploy(deploy_env, SHA)
    assert res.returncode == 0, res.stderr
    assert f"Deployed {NEW_IMAGE}" in res.stdout
    assert state(deploy_env) == NEW_IMAGE
    calls = docker_calls(deploy_env)
    assert calls[0].endswith("login ghcr.io -u github-actions --password-stdin")
    assert f"SJEKKA_IMAGE={NEW_IMAGE} compose pull app" in calls
    assert any(c.endswith("image prune -f") for c in calls)


def test_unhealthy_deploy_rolls_back_to_previous_image(deploy_env):
    Path(deploy_env["SJEKKA_DIR"], "state/deployed-image").write_text(OLD_IMAGE + "\n")
    res = deploy(deploy_env, SHA, FAKE_UNHEALTHY=NEW_IMAGE)
    assert res.returncode == 1
    assert f"Rolling back to {OLD_IMAGE}" in res.stderr
    assert Path(deploy_env["FAKE_RUNNING"]).read_text().strip() == OLD_IMAGE
    assert state(deploy_env) == OLD_IMAGE  # still points at what's running
    assert not any(c.endswith("image prune -f") for c in docker_calls(deploy_env))


def test_unhealthy_first_deploy_fails_without_rollback(deploy_env):
    res = deploy(deploy_env, SHA, FAKE_UNHEALTHY=NEW_IMAGE)
    assert res.returncode == 1
    assert "Rolling back" not in res.stderr
    assert state(deploy_env) is None


def test_deploy_leaves_no_registry_credentials(deploy_env, tmp_path):
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    deploy(deploy_env, SHA, TMPDIR=str(tmp))
    assert list(tmp.iterdir()) == []


# backup.sh

@pytest.fixture
def backup_dir(tmp_path):
    (tmp_path / "data").mkdir()
    return tmp_path


def backup(directory, **extra_env):
    return subprocess.run(
        ["bash", str(ROOT / "deploy/backup.sh")],
        capture_output=True, text=True, env=os.environ | {"SJEKKA_DIR": str(directory)} | extra_env,
    )


def make_db(path, rows=3):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (n INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    con.commit()
    return con


def test_backup_copies_database_and_can_be_restored(backup_dir, tmp_path):
    make_db(backup_dir / "data/sjekka.db").close()
    res = backup(backup_dir)
    assert res.returncode == 0, res.stderr

    [archive] = (backup_dir / "backups").glob("sjekka-*.db.gz")
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(archive.read_bytes()))
    assert sqlite3.connect(restored).execute("SELECT count(*) FROM t").fetchone() == (3,)
    assert oct((backup_dir / "backups").stat().st_mode)[-3:] == "700"


def test_backup_works_while_the_app_holds_a_write_transaction(backup_dir):
    con = make_db(backup_dir / "data/sjekka.db")
    con.execute("BEGIN")
    con.execute("INSERT INTO t VALUES (99)")  # uncommitted
    res = backup(backup_dir)
    con.rollback()
    assert res.returncode == 0, res.stderr


def test_backup_deletes_copies_older_than_retention(backup_dir):
    make_db(backup_dir / "data/sjekka.db").close()
    old = backup_dir / "backups/sjekka-2000-01-01.db.gz"
    recent = backup_dir / "backups/sjekka-2000-01-02.db.gz"
    old.parent.mkdir()
    for f, days in ((old, 20), (recent, 5)):
        f.write_bytes(b"")
        subprocess.run(["touch", "-d", f"{days} days ago", str(f)], check=True)

    assert backup(backup_dir).returncode == 0
    assert not old.exists()
    assert recent.exists()


def test_backup_without_database_is_a_no_op(backup_dir):
    res = backup(backup_dir)
    assert res.returncode == 0
    assert not (backup_dir / "backups").exists()
