"""Static contracts for MarketConnector CodeBuild configuration."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDSPEC = ROOT / ".devops" / "codebuild" / "buildspec.yml"
WORKFLOW = (
    ROOT
    / ".github"
    / "workflows"
    / "marketconnector-codebuild.yml"
)


def test_codebuild_files_exist() -> None:
    assert BUILDSPEC.is_file()
    assert WORKFLOW.is_file()


def test_buildspec_runs_quality_gate() -> None:
    content = BUILDSPEC.read_text(encoding="utf-8-sig")

    required = (
        "python .devops/scripts/compile_check.py",
        "python -m pytest",
        "python -m ruff check .devops/scripts tests",
        "python .devops/scripts/build_bundle.py",
        "PUSH_ARTIFACT",
        "ARTIFACT_BUCKET",
    )

    for value in required:
        assert value in content


def test_workflow_uses_oidc_and_exact_project() -> None:
    content = WORKFLOW.read_text(encoding="utf-8-sig")

    required = (
        "id-token: write",
        "AWS_CODEBUILD_ROLE_ARN",
        "portfolio-marketconnector-build",
        "--source-version",
        "PUSH_ARTIFACT",
    )

    for value in required:
        assert value in content


def test_workflow_automates_release_to_marketconnector_ec2() -> None:
    content = WORKFLOW.read_text(encoding="utf-8-sig")

    required = (
        "MARKETCONNECTOR_INSTANCE_ID",
        "aws ec2 describe-instances",
        "aws ec2 start-instances",
        "aws ec2 stop-instances",
        "aws ssm describe-instance-information",
        "portfolio-marketconnector",
        "portfolio-marketconnector-ec2",
        "aws deploy create-deployment",
        "aws deploy get-deployment",
        "started_by_workflow",
        "Restore EC2 state",
    )

    for value in required:
        assert value in content


def test_workflow_release_remains_no_order() -> None:
    content = WORKFLOW.read_text(encoding="utf-8-sig").lower()

    forbidden = (
        "ssm send-command",
        "connector_buy.py",
        "connector_sell.py",
        "connector_cancel.py",
        "connector_modify.py",
        "connector_strategy_order_execute.py",
        "--execute",
    )

    for value in forbidden:
        assert value not in content