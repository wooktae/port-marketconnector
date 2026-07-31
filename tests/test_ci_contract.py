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


def test_workflow_does_not_deploy_to_ec2() -> None:
    content = WORKFLOW.read_text(encoding="utf-8-sig").lower()

    forbidden = (
        "codedeploy create-deployment",
        "ssm send-command",
        "ec2 start-instances",
        "connector_buy.py",
        "connector_sell.py",
        "--execute",
    )

    for value in forbidden:
        assert value not in content