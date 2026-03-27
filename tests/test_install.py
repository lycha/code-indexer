"""Tests for the index install command."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from indexer.cli import cli


def _make_runner() -> CliRunner:
    """Create a CliRunner with stderr separation."""
    return CliRunner()


class TestInstallCommand:
    """Tests for the install subcommand."""

    def test_install_rg_already_present(self):
        """When ripgrep is already installed, exit 0 and report it."""
        runner = _make_runner()
        with patch("indexer.cli.shutil.which", return_value="/usr/local/bin/rg"):
            result = runner.invoke(cli, ["install"])

        assert result.exit_code == 0
        assert "[OK] ripgrep is already installed" in result.stderr

    def test_install_no_rg_no_package_manager(self):
        """When no ripgrep and no package manager found, exit 2 with error."""
        runner = _make_runner()

        def which_side_effect(name: str) -> str | None:
            # Return None for everything: rg and all package managers
            return None

        with (
            patch("indexer.cli.shutil.which", side_effect=which_side_effect),
            patch("indexer.cli.platform.system", return_value="Linux"),
        ):
            result = runner.invoke(cli, ["install"])

        assert result.exit_code == 2
        assert "No supported package manager" in result.stderr

    def test_install_brew_success(self):
        """When brew is available on Darwin and install succeeds, exit 0."""
        runner = _make_runner()

        # Track call count to shutil.which("rg"):
        # first call -> None (not installed), last call -> path (installed)
        rg_call_count = 0

        def which_side_effect(name: str) -> str | None:
            nonlocal rg_call_count
            if name == "rg":
                rg_call_count += 1
                if rg_call_count == 1:
                    return None  # Not installed yet
                return "/usr/local/bin/rg"  # Installed after brew
            if name == "brew":
                return "/usr/local/bin/brew"
            return None

        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch("indexer.cli.shutil.which", side_effect=which_side_effect),
            patch("indexer.cli.platform.system", return_value="Darwin"),
            patch("indexer.cli.subprocess.run", return_value=mock_result) as mock_run,
        ):
            result = runner.invoke(cli, ["install"])

        assert result.exit_code == 0
        assert "installed successfully" in result.stderr
        # Verify brew install was called
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == ["brew", "install", "ripgrep"]

    def test_install_brew_failure(self):
        """When brew install fails, exit 2 with error."""
        runner = _make_runner()

        def which_side_effect(name: str) -> str | None:
            if name == "rg":
                return None
            if name == "brew":
                return "/usr/local/bin/brew"
            return None

        mock_result = MagicMock()
        mock_result.returncode = 1

        with (
            patch("indexer.cli.shutil.which", side_effect=which_side_effect),
            patch("indexer.cli.platform.system", return_value="Darwin"),
            patch("indexer.cli.subprocess.run", return_value=mock_result),
        ):
            result = runner.invoke(cli, ["install"])

        assert result.exit_code == 2
        assert "install failed" in result.stderr

    def test_install_unsupported_platform(self):
        """When platform is unsupported, exit 2 with error."""
        runner = _make_runner()

        with (
            patch("indexer.cli.shutil.which", return_value=None),
            patch("indexer.cli.platform.system", return_value="FreeBSD"),
        ):
            result = runner.invoke(cli, ["install"])

        assert result.exit_code == 2
        assert "Unsupported platform" in result.stderr
