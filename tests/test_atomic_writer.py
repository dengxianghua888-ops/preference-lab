"""Real filesystem regressions for single-file publication and metadata safety."""

from __future__ import annotations

import errno
import json
import os
import stat
import struct
import subprocess
import sys
import threading
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from ecoalign_forge.export.sharegpt_format import (
    export_dataset_info,
    export_sharegpt,
    to_sharegpt_dict,
)
from ecoalign_forge.runtime import journal
from ecoalign_forge.schemas.dpo import DPO_Pair


def pair(text="chosen"):
    return DPO_Pair(
        prompt="联系 🐦",
        chosen=text,
        rejected="bad",
        chosen_score=1,
        rejected_score=0,
        preference_gap=1,
        dimension="contact",
        difficulty="easy",
        source_case_id="one",
    )


@pytest.mark.parametrize("kind", ["sharegpt", "info", "text"])
def test_predictable_sidecar_symlink_is_untouched(tmp_path, kind):
    target = tmp_path / ("dataset_info.json" if kind == "info" else "train_sharegpt.json")
    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"unrelated private contents")
    sidecar = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    sidecar.symlink_to(unrelated)
    if kind == "sharegpt":
        export_sharegpt([pair()], target)
    elif kind == "info":
        export_dataset_info(tmp_path)
    else:
        journal.atomic_write(target, "complete")
    assert unrelated.read_bytes() == b"unrelated private contents"
    assert sidecar.is_symlink()
    assert not target.is_symlink()
    assert set(tmp_path.iterdir()) == {target, unrelated, sidecar}


@pytest.mark.parametrize("kind", ["sharegpt", "info", "text"])
@pytest.mark.parametrize(
    "mask,existing_mode",
    [(0o022, 0o600), (0o022, 0o640), (0o002, 0o660), (0o077, None), (0o022, None), (0o002, None)],
)
def test_modes_use_os_creation_policy_and_preserve_existing(tmp_path, kind, mask, existing_mode):
    # A child owns its umask; the application/test runner never changes its own.
    code = """import os, pathlib, stat, sys
from ecoalign_forge.runtime.journal import atomic_write
from ecoalign_forge.export.sharegpt_format import export_dataset_info, export_sharegpt
root=pathlib.Path(sys.argv[1]); kind=sys.argv[2]; mask=int(sys.argv[3]); mode=sys.argv[4]
os.umask(mask)
p=root/('dataset_info.json' if kind=='info' else 'train_sharegpt.json')
if mode!='None':
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,int(mode)); os.close(fd)
    p.chmod(int(mode))
if kind=='info': export_dataset_info(root)
elif kind=='sharegpt': export_sharegpt([],p)
else: atomic_write(p,'complete')
assert stat.S_IMODE(p.stat().st_mode)==(0o666&~mask if mode=='None' else int(mode))
assert list(root.iterdir())==[p]
"""
    subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), kind, str(mask), str(existing_mode)], check=True
    )


def test_staging_is_private_before_candidate_open_and_during_write(tmp_path):
    target = tmp_path / "output"
    real_open = os.open
    opened = []

    def observe(path, flags, mode=0o777, *, dir_fd=None):
        if path == "content":
            stage = next(tmp_path.glob(".atomic-*"))
            assert stat.S_IMODE(stage.stat().st_mode) & 0o777 == 0o700
            assert flags & os.O_EXCL and flags & os.O_CREAT
            assert dir_fd is not None
            opened.append(stage)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    with (
        patch.object(journal.os, "open", side_effect=observe),
        journal.atomic_writer(target) as stream,
    ):
        assert stat.S_IMODE(os.fstat(stream.fileno()).st_mode) & 0o177 == 0
        stream.write("private until publication")
        assert not target.exists()
    assert len(opened) == 1
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize(
    "failure",
    ["partial", "fdopen", "fsync", "replace", "acl_read", "acl_restore", "metadata_verify"],
)
def test_prepublication_failure_keeps_old_bytes_and_closes_fds(tmp_path, failure):
    target = tmp_path / "output"
    target.write_bytes(b"previous")
    target.chmod(0o640)
    descriptors = []
    real_open, real_acl, real_fstat = os.open, journal._access_acl, os.fstat

    def capture(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        descriptors.append(fd)
        return fd

    def acl_read(path):
        if not isinstance(path, int):
            raise OSError(errno.EACCES, "cannot read previous ACL")
        return real_acl(path)

    fstats = 0

    def wrong_metadata(fd):
        nonlocal fstats
        result = real_fstat(fd)
        fstats += 1
        if fstats == 4:  # stage, creation, current, restored
            values = list(result)
            values[0] ^= 0o020
            return os.stat_result(values)
        return result

    patches = {
        "partial": patch.object(journal, "_ACCESS_ACL", journal._ACCESS_ACL),
        "fdopen": patch.object(journal.os, "fdopen", side_effect=OSError("fdopen failure")),
        "fsync": patch.object(journal.os, "fsync", side_effect=OSError("file fsync failure")),
        "replace": patch.object(journal.os, "replace", side_effect=OSError("replace failure")),
        "acl_read": patch.object(journal, "_access_acl", side_effect=acl_read),
        "acl_restore": patch.object(
            journal, "_restore_access_acl", side_effect=OSError("ACL restore failure")
        ),
        "metadata_verify": patch.object(journal.os, "fstat", side_effect=wrong_metadata),
    }
    with (
        patch.object(journal.os, "open", side_effect=capture),
        patches[failure],
        pytest.raises(OSError),
        journal.atomic_writer(target) as stream,
    ):
        stream.write("partial replacement")
        if failure == "partial":
            raise OSError("simulated full disk while serializing")
    assert target.read_bytes() == b"previous"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert list(tmp_path.iterdir()) == [target]
    for fd in descriptors:
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(fd)


def test_cleanup_failure_does_not_hide_serialization_error(tmp_path):
    target = tmp_path / "output"
    real_unlink = os.unlink

    def unlink_then_fail(*args, **kwargs):
        real_unlink(*args, **kwargs)
        raise OSError("cleanup error after successful unlink")

    with (
        patch.object(journal.os, "unlink", side_effect=unlink_then_fail),
        pytest.raises(ValueError, match="primary serialization"),
        journal.atomic_writer(target) as stream,
    ):
        stream.write("partial")
        raise ValueError("primary serialization")
    assert not list(tmp_path.iterdir())


def test_postreplace_directory_fsync_error_leaves_complete_new_file(tmp_path):
    target = tmp_path / "output"
    target.write_text("old")
    real_fsync = os.fsync

    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("parent directory fsync failure")
        real_fsync(fd)

    with (
        patch.object(journal.os, "fsync", side_effect=fail_directory),
        pytest.raises(OSError, match="parent directory"),
    ):
        journal.atomic_write(target, "complete replacement")
    assert target.read_text() == "complete replacement"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "special_mode"])
def test_nonordinary_existing_target_fails_without_touching_it(tmp_path, kind):
    target, original = tmp_path / "output", tmp_path / "original"
    original.write_text("old")
    if kind == "symlink":
        target.symlink_to(original)
    elif kind == "hardlink":
        os.link(original, target)
    else:
        target.write_text("old")
        target.chmod(0o2600)
    before = target.lstat()
    with pytest.raises(ValueError, match="regular single-link"):
        journal.atomic_write(target, "new")
    assert target.read_text() == original.read_text() == "old"
    assert target.lstat().st_mode == before.st_mode
    assert set(tmp_path.iterdir()) == {target, original}


def test_concurrent_writers_publish_complete_last_writer_without_shared_temp(tmp_path):
    target = tmp_path / "output"
    barrier = threading.Barrier(2)
    payloads = ["a" * 100_000, "b" * 100_000]

    def write(value):
        with journal.atomic_writer(target) as stream:
            stream.write(value[:50_000])
            barrier.wait(timeout=10)
            assert len(list(tmp_path.glob(".atomic-*"))) == 2
            barrier.wait(timeout=10)
            stream.write(value[50_000:])

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(write, payloads))
    assert target.read_text() in payloads
    assert list(tmp_path.iterdir()) == [target]


def test_large_sharegpt_is_streamed_with_identical_bytes(tmp_path):
    pairs = [
        pair("a" * (512 * 1024)).model_copy(update={"rejected": "b" * (512 * 1024)})
        for _ in range(16)
    ]
    expected, target = tmp_path / "expected", tmp_path / "output"
    with expected.open("w", encoding="utf-8") as stream:
        json.dump([to_sharegpt_dict(p) for p in pairs], stream, ensure_ascii=False, indent=2)
    tracemalloc.start()
    export_sharegpt(pairs, target)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert target.stat().st_size > 16 * 1024 * 1024
    # Bounded finite workload, not an OOM claim. json.dumps peaked above 80 MB.
    assert peak < 4 * 1024 * 1024
    assert target.read_bytes() == expected.read_bytes()


def test_nonlinux_does_not_probe_linux_acl_namespace(tmp_path):
    with (
        patch.object(journal.sys, "platform", "darwin"),
        patch.object(
            journal.os, "getxattr", side_effect=AssertionError("Linux ACL probe on non-Linux")
        ),
    ):
        journal.atomic_write(tmp_path / "output", "complete")


@pytest.fixture
def real_linux_acl(tmp_path):
    if sys.platform != "linux":
        pytest.skip("Linux POSIX ACL integration only")
    if not (
        os.environ.get("GITHUB_ACTIONS") == "true"
        or os.environ.get("ECOALIGN_TEST_POSIX_ACL") == "1"
    ):
        pytest.skip(
            "Cloud ACL setup previously ENOTSUP/EINVAL; NOT_RUN locally. CI requires real ACL fixture; other supported Linux hosts can opt in with ECOALIGN_TEST_POSIX_ACL=1"
        )

    # Named UID is mapped current UID, not an unmapped container UID.
    def acl(owner=6, mask=4):
        entries = [
            (1, owner, 0xFFFFFFFF),
            (2, 4, os.getuid()),
            (4, 0, 0xFFFFFFFF),
            (16, mask, 0xFFFFFFFF),
            (32, 0, 0xFFFFFFFF),
        ]
        return struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *entry) for entry in entries)

    probe = tmp_path / "acl-probe"
    probe.touch()
    os.setxattr(
        probe, journal._ACCESS_ACL, acl()
    )  # CI must fail, not skip, if this fixture cannot execute.
    assert os.getxattr(probe, journal._ACCESS_ACL) == acl()
    probe.unlink()
    return acl


def test_real_linux_existing_access_acl_preserved(tmp_path, real_linux_acl):
    target = tmp_path / "output"
    target.write_text("old")
    os.setxattr(target, journal._ACCESS_ACL, real_linux_acl())
    before = target.stat()
    acl = os.getxattr(target, journal._ACCESS_ACL)
    journal.atomic_write(target, "new")
    after = target.stat()
    assert (after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)) == (
        before.st_uid,
        before.st_gid,
        stat.S_IMODE(before.st_mode),
    )
    assert os.getxattr(target, journal._ACCESS_ACL) == acl


def test_real_linux_new_default_acl_and_sgid_policy_preserved(tmp_path, real_linux_acl):
    os.setxattr(tmp_path, "system.posix_acl_default", real_linux_acl(owner=7))
    tmp_path.chmod(0o2700)
    # Direct creation is the compatibility oracle, including inherited named ACLs.
    control = tmp_path / "control"
    fd = os.open(control, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
    os.close(fd)
    target = tmp_path / "output"
    journal.atomic_write(target, "new")
    assert stat.S_IMODE(target.stat().st_mode) == stat.S_IMODE(control.stat().st_mode)
    assert target.stat().st_gid == control.stat().st_gid == tmp_path.stat().st_gid
    assert os.getxattr(target, journal._ACCESS_ACL) == os.getxattr(control, journal._ACCESS_ACL)


def test_real_linux_old_file_does_not_gain_inherited_named_acl(tmp_path, real_linux_acl):
    target = tmp_path / "output"
    target.write_text("old")
    target.chmod(0o600)
    os.setxattr(tmp_path, "system.posix_acl_default", real_linux_acl(owner=7))
    journal.atomic_write(target, "new")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with pytest.raises(OSError) as exc:
        os.getxattr(target, journal._ACCESS_ACL)
    assert exc.value.errno == errno.ENODATA


def test_owner_group_preservation_failure_keeps_previous_target(tmp_path):
    target = tmp_path / "output"
    target.write_text("old")
    target.chmod(0o600)
    original_lstat = Path.lstat

    def different_group(path, *args, **kwargs):
        result = original_lstat(path, *args, **kwargs)
        if path == target:
            fields = list(result)
            fields[5] += 1
            return os.stat_result(fields)
        return result

    with (
        patch.object(Path, "lstat", different_group),
        patch.object(journal.os, "fchown", side_effect=PermissionError("cannot preserve group")),
        pytest.raises(PermissionError, match="cannot preserve group"),
    ):
        journal.atomic_write(target, "new")
    assert target.read_text() == "old"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [target]


def test_exclusive_candidate_collision_does_not_delete_unowned_file(tmp_path):
    target = tmp_path / "output"
    real_open = os.open
    collided = []

    def collide(path, flags, mode=0o777, *, dir_fd=None):
        if path == "content":
            stage = next(tmp_path.glob(".atomic-*"))
            (stage / "content").write_text("preexisting")
            collided.append(stage)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    with patch.object(journal.os, "open", side_effect=collide), pytest.raises(FileExistsError):
        journal.atomic_write(target, "new")
    assert not target.exists()
    assert (collided[0] / "content").read_text() == "preexisting"
    # This controlled collision is outside the same-UID isolation guarantee.
    (collided[0] / "content").unlink()
    collided[0].rmdir()


@pytest.mark.parametrize("primary", [False, True])
def test_stream_close_failure_is_prepublication_and_preserves_primary_error(tmp_path, primary):
    target = tmp_path / "output"
    target.write_text("old")
    real_fdopen = os.fdopen

    class CloseFailure:
        def __init__(self, *args, **kwargs):
            self.stream = real_fdopen(*args, **kwargs)

        def __getattr__(self, key):
            return getattr(self.stream, key)

        def close(self):
            self.stream.close()
            raise OSError("close failure")

    with (
        patch.object(journal.os, "fdopen", CloseFailure),
        pytest.raises(
            ValueError if primary else OSError, match="primary" if primary else "close failure"
        ),
        journal.atomic_writer(target) as stream,
    ):
        stream.write("new")
        if primary:
            raise ValueError("primary serialization error")
    assert target.read_text() == "old"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("fsync_fails", [False, True])
def test_parent_close_failure_preserves_fsync_error_and_closes_fd(tmp_path, fsync_fails):
    target = tmp_path / "output"
    target.write_text("old")
    real_fsync, real_close = os.fsync, os.close
    parent_descriptors = []
    primary = OSError(errno.EIO, "primary parent directory fsync failure")
    secondary = OSError(errno.EIO, "secondary parent directory close failure")

    def fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            parent_descriptors.append(fd)
            if fsync_fails:
                raise primary
        real_fsync(fd)

    def close(fd):
        real_close(fd)
        if fd in parent_descriptors:
            raise secondary

    with (
        patch.object(journal.os, "fsync", side_effect=fsync),
        patch.object(journal.os, "close", side_effect=close),
        pytest.raises(OSError) as caught,
    ):
        journal.atomic_write(target, "complete new file")
    assert caught.value is (primary if fsync_fails else secondary)
    assert target.read_text() == "complete new file"
    assert list(tmp_path.iterdir()) == [target]
    assert len(parent_descriptors) == 1
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(parent_descriptors[0])
