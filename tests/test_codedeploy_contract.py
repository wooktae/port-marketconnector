from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE_FILE = ROOT / ".devops" / "bundle" / "include.txt"
APPSPEC = ROOT / "appspec.yml"
HOOK_DIR = ROOT / "codedeploy"


def test_codedeploy_files_are_bundled() -> None:
    entries = {
        line.strip()
        for line in INCLUDE_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }

    required = {
        "appspec.yml",
        "codedeploy/application_stop.sh",
        "codedeploy/before_install.sh",
        "codedeploy/after_install.sh",
        "codedeploy/application_start.sh",
        "codedeploy/validate_service.sh",
    }

    assert required <= entries


def test_appspec_has_required_hooks() -> None:
    source = APPSPEC.read_text(encoding="utf-8")

    for hook in (
        "ApplicationStop",
        "BeforeInstall",
        "AfterInstall",
        "ApplicationStart",
        "ValidateService",
    ):
        assert hook in source


def test_hooks_preserve_runtime_and_block_orders() -> None:
    after_install = (HOOK_DIR / "after_install.sh").read_text(encoding="utf-8")

    validate = (HOOK_DIR / "validate_service.sh").read_text(encoding="utf-8")

    assert "access_token.txt" not in after_install
    assert "ORDER_API_CALLED=NO" in validate
    assert "deployment.lock" in validate
