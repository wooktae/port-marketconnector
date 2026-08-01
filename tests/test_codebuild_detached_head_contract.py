from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BUILD_SCRIPT = (
    ROOT
    / ".devops"
    / "scripts"
    / "build_bundle.py"
)

BUILDSPEC = (
    ROOT
    / ".devops"
    / "codebuild"
    / "buildspec.yml"
)


def test_build_bundle_allows_verified_detached_head() -> None:
    source = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert 'if branch and branch != "main":' in source
    assert "if not branch and not expected_sha:" in source
    assert "Detached HEAD requires --expected-sha" in source
    assert '"source_branch": "main"' in source
    assert '"checkout_mode":' not in source
    assert "CHECKOUT_MODE=" in source


def test_post_build_blocks_false_success() -> None:
    source = BUILDSPEC.read_text(encoding="utf-8")

    guard = (
        'if [ "${CODEBUILD_BUILD_SUCCEEDING:-0}" '
        '!= "1" ]; then'
    )
    failure = (
        "MARKETCONNECTOR_CODEBUILD="
        "FAILED_BEFORE_POST_BUILD"
    )
    success = "MARKETCONNECTOR_CODEBUILD=SUCCESS"

    assert guard in source
    assert failure in source
    assert source.index(guard) < source.index(success)

def test_bundle_reads_committed_git_blobs() -> None:
    source = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert (
        "def read_git_blob(relative_path: Path) -> bytes:"
        in source
    )
    assert '"git",' in source
    assert '"show",' in source
    assert (
        'f"HEAD:{relative_path.as_posix()}"'
        in source
    )
    assert (
        "content = read_git_blob(relative_path)"
        in source
    )
    assert (
        "(ROOT / relative_path).read_bytes()"
        not in source
    )
