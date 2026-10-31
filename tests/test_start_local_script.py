from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
START_SCRIPT = PROJECT_ROOT / "scripts" / "start-local.ps1"


def test_start_script_defaults_to_full_compose_profiles() -> None:
    script = START_SCRIPT.read_text(encoding="utf-8")

    assert '[string]$Mode = "full"' in script
    assert '"--profile", "full"' in script
    assert '"--profile", "state"' in script
    assert '"--profile", "ranking"' in script
    assert "if ($WithRanking)" in script
    assert "Application images are missing" in script
    assert "config --images" in script
    assert "$composeProjectName-backend" in script
    assert "if ($RunE2E)" in script
    assert "pnpm run test:e2e" in script
    assert "Wait-DockerEngine" in script


def test_start_script_keeps_host_mode_and_dry_run() -> None:
    script = START_SCRIPT.read_text(encoding="utf-8")

    assert '[ValidateSet("full", "host")]' in script
    assert "$Mode -eq \"full\"" in script
    assert "$DryRun" in script
