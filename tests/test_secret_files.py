import os
from pathlib import Path
import stat
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from services.api.environment_domain import ConfigurationCategory
from services.api.environment_repository import ResolvedSecretValue
from services.secret_files import LinuxTmpfsVerifier, SecretFileStager, SecretStagingError


def value(
    name: str = "TOKEN",
    plaintext: str = "secret-value",
    category: ConfigurationCategory = ConfigurationCategory.ENVIRONMENT_VARIABLE,
) -> ResolvedSecretValue:
    return ResolvedSecretValue(category, name, uuid4(), plaintext)


def stager(root: Path, **kwargs) -> SecretFileStager:
    return SecretFileStager(
        root, verifier=lambda candidate: None,
        ownership_operation=lambda path, uid, gid: None,
        **kwargs,
    )


def test_stages_exact_layout_modes_and_cleans_only_child(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    sentinel = root / "keep"
    sentinel.write_text("root-owned", encoding="utf-8")
    ownership = Mock()
    subject = SecretFileStager(
        root, verifier=lambda candidate: None, runner_uid=123, runner_gid=456,
        ownership_operation=ownership,
    )

    with subject.stage((
        value(plaintext="påssword"),
        value("api-key", "parameter", ConfigurationCategory.COMMON_PARAMETER),
    )) as child:
        assert child.parent == root and child != root
        assert (child / "environment_variable" / "TOKEN").read_text("utf-8") == "påssword"
        assert (child / "common_parameter" / "api-key").read_text("utf-8") == "parameter"
        assert {item.name for item in child.iterdir()} == {
            "environment_variable", "common_parameter"
        }
        if os.name != "nt":
            assert stat.S_IMODE(child.stat().st_mode) == 0o700
            assert stat.S_IMODE((child / "environment_variable" / "TOKEN").stat().st_mode) == 0o600

    assert sentinel.read_text("utf-8") == "root-owned"
    assert list(root.iterdir()) == [sentinel]
    assert all(call.args[1:] == (123, 456) for call in ownership.call_args_list)


def test_cleans_child_when_context_body_raises(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(RuntimeError, match="body failed"):
        with stager(root).stage((value(),)):
            raise RuntimeError("body failed")
    assert root.is_dir() and not list(root.iterdir())


@pytest.mark.parametrize("items", [
    (value("../escape"),),
    (value("TOKEN", "one"), value("TOKEN", "other")),
    (value("TOKEN", "x" * 9),),
])
def test_rejects_traversal_duplicates_and_oversize_without_disclosure(
    tmp_path: Path, items: tuple[ResolvedSecretValue, ...]
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    subject = stager(root, maximum_secret_bytes=8)
    with pytest.raises(SecretStagingError) as raised:
        with subject.stage(items):
            pass
    assert str(raised.value) == "secret staging failed"
    assert "other" not in repr(raised.value)
    assert not list(root.iterdir())


def test_files_are_created_exclusively_without_following_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with patch("services.secret_files.os.open", wraps=os.open) as opened:
        with stager(root).stage((value(),)):
            pass
    flags = opened.call_args.args[1]
    assert flags & os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        assert flags & os.O_NOFOLLOW


def test_cleanup_failure_is_generic(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    plaintext = "must-not-leak"
    with (
        patch("services.secret_files.shutil.rmtree", side_effect=OSError(plaintext)),
        pytest.raises(SecretStagingError) as raised,
    ):
        with stager(root).stage((value(plaintext=plaintext),)):
            pass
    assert str(raised.value) == "secret staging failed"
    assert plaintext not in repr(raised.value)
    assert str(root) not in repr(raised.value)


def test_linux_verifier_uses_deepest_mount_and_rejects_invalid_roots(tmp_path: Path) -> None:
    root = tmp_path / "secret-root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    mountinfo = tmp_path / "mountinfo"
    escaped_root = str(root.resolve()).replace(" ", r"\040")
    escaped_nested = str(nested.resolve()).replace(" ", r"\040")
    mountinfo.write_text(
        f"1 0 0:1 / {escaped_root} rw - tmpfs tmpfs rw\n"
        f"2 1 0:2 / {escaped_nested} rw - ext4 disk rw\n",
        encoding="utf-8",
    )
    verifier = LinuxTmpfsVerifier(mountinfo)
    verifier.verify(root)
    with pytest.raises(SecretStagingError):
        verifier.verify(nested)
    with pytest.raises(SecretStagingError):
        verifier.verify(tmp_path / "missing")
    regular_file = tmp_path / "file"
    regular_file.touch()
    with pytest.raises(SecretStagingError):
        verifier.verify(regular_file)

    link = tmp_path / "root-link"
    try:
        link.symlink_to(root, target_is_directory=True)
    except OSError:
        return
    with pytest.raises(SecretStagingError):
        verifier.verify(link)