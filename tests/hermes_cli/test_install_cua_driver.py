"""Tests for ``install_cua_driver`` upgrade semantics and supply-chain guardrails.

Hermes installs cua-driver from pinned GitHub release assets. The installer
must never pipe a mutable remote script into a shell. ``install_cua_driver(upgrade=True)``
must:

* Be macOS-only — no-op silently on Linux/Windows so ``hermes update`` can
  call it unconditionally without warning every non-macOS user.
* Re-run the pinned asset installer even when the binary is already on PATH
  (this is the fix for the "we only pulled cua-driver once on enable" complaint).
* Preserve original ``upgrade=False`` behaviour for the toolset-enable flow:
  skip if installed, install otherwise, warn on non-macOS.
* Fail closed if the selected release asset is not pinned and hash-verified.
"""

from __future__ import annotations

import io
import tarfile
from unittest.mock import patch

import pytest


class TestInstallCuaDriverUpgrade:
    def test_upgrade_on_non_macos_is_silent_noop(self):
        from hermes_cli import tools_config

        with patch.object(tools_config, "_print_warning") as warn, \
             patch("platform.system", return_value="Linux"):
            assert tools_config.install_cua_driver(upgrade=True) is False
            warn.assert_not_called()

    def test_non_upgrade_on_non_macos_warns(self):
        from hermes_cli import tools_config

        with patch.object(tools_config, "_print_warning") as warn, \
             patch("platform.system", return_value="Linux"):
            assert tools_config.install_cua_driver(upgrade=False) is False
            warn.assert_called()

    def test_upgrade_on_macos_with_binary_runs_installer(self):
        from hermes_cli import tools_config

        with patch("platform.system", return_value="Darwin"), \
             patch.object(tools_config.shutil, "which",
                          side_effect=lambda n: "/usr/local/bin/" + n
                                                 if n in {"cua-driver", "curl"} else None), \
             patch.object(tools_config, "_check_cua_driver_asset_for_arch",
                          return_value=True), \
             patch.object(tools_config, "_run_cua_driver_installer",
                          return_value=True) as runner, \
             patch("subprocess.run"):
            assert tools_config.install_cua_driver(upgrade=True) is True
            runner.assert_called_once()
            kwargs = runner.call_args.kwargs
            assert kwargs.get("verbose") is False

    def test_upgrade_on_macos_without_binary_runs_installer(self):
        from hermes_cli import tools_config

        with patch("platform.system", return_value="Darwin"), \
             patch.object(tools_config.shutil, "which",
                          side_effect=lambda n: "/usr/bin/curl" if n == "curl" else None), \
             patch.object(tools_config, "_check_cua_driver_asset_for_arch",
                          return_value=True), \
             patch.object(tools_config, "_run_cua_driver_installer",
                          return_value=True) as runner:
            assert tools_config.install_cua_driver(upgrade=True) is True
            runner.assert_called_once()

    def test_non_upgrade_on_macos_with_binary_skips_install(self):
        from hermes_cli import tools_config

        with patch("platform.system", return_value="Darwin"), \
             patch.object(tools_config.shutil, "which",
                          side_effect=lambda n: "/usr/local/bin/" + n
                                                 if n in {"cua-driver", "curl"} else None), \
             patch.object(tools_config, "_run_cua_driver_installer") as runner, \
             patch("subprocess.run"):
            assert tools_config.install_cua_driver(upgrade=False) is True
            runner.assert_not_called()

    def test_non_upgrade_on_macos_without_binary_runs_installer(self):
        from hermes_cli import tools_config

        with patch("platform.system", return_value="Darwin"), \
             patch.object(tools_config.shutil, "which",
                          side_effect=lambda n: "/usr/bin/curl" if n == "curl" else None), \
             patch.object(tools_config, "_check_cua_driver_asset_for_arch",
                          return_value=True), \
             patch.object(tools_config, "_run_cua_driver_installer",
                          return_value=True) as runner:
            assert tools_config.install_cua_driver(upgrade=False) is True


class TestCheckCuaDriverAssetForArch:
    def test_arm64_always_returns_true(self):
        from hermes_cli import tools_config

        with patch("platform.machine", return_value="arm64"):
            assert tools_config._check_cua_driver_asset_for_arch() is True

    def test_x86_64_supported_by_pinned_universal_asset(self):
        from hermes_cli import tools_config

        with patch("platform.machine", return_value="x86_64"), \
             patch("urllib.request.urlopen") as urlopen:
            assert tools_config._check_cua_driver_asset_for_arch() is True
            urlopen.assert_not_called()

    def test_unsupported_arch_returns_false(self):
        from hermes_cli import tools_config

        with patch("platform.machine", return_value="ppc64"), \
             patch("urllib.request.urlopen") as urlopen, \
             patch.object(tools_config, "_print_warning") as warn, \
             patch.object(tools_config, "_print_info"):
            assert tools_config._check_cua_driver_asset_for_arch() is False
            urlopen.assert_not_called()
            warn.assert_called_once()
            assert "ppc64" in warn.call_args[0][0]

    def test_fresh_install_x86_64_runs_pinned_installer(self):
        """Intel macOS uses the pinned universal release asset."""
        from hermes_cli import tools_config

        with patch("platform.system", return_value="Darwin"), \
             patch.object(tools_config.shutil, "which",
                          side_effect=lambda n: "/usr/bin/curl" if n == "curl" else None), \
             patch("platform.machine", return_value="x86_64"), \
             patch.object(tools_config, "_run_cua_driver_installer", return_value=True) as runner:
            assert tools_config.install_cua_driver(upgrade=False) is True
            runner.assert_called_once()

    def test_upgrade_unsupported_arch_returns_existing_status(self):
        """On unsupported arch, return whether binary existed and skip install."""
        from hermes_cli import tools_config

        # With binary installed — returns True (binary exists)
        with patch("platform.system", return_value="Darwin"), \
             patch.object(tools_config.shutil, "which",
                          side_effect=lambda n: "/usr/local/bin/" + n
                                                 if n in ("cua-driver", "curl") else None), \
             patch("platform.machine", return_value="ppc64"), \
             patch.object(tools_config, "_print_warning"), \
             patch.object(tools_config, "_print_info"), \
             patch.object(tools_config, "_run_cua_driver_installer") as runner:
            assert tools_config.install_cua_driver(upgrade=True) is True
            runner.assert_not_called()

        # Without binary — returns False
        with patch("platform.system", return_value="Darwin"), \
             patch.object(tools_config.shutil, "which",
                          side_effect=lambda n: "/usr/bin/curl" if n == "curl" else None), \
             patch("platform.machine", return_value="ppc64"), \
             patch.object(tools_config, "_print_warning"), \
             patch.object(tools_config, "_print_info"), \
             patch.object(tools_config, "_run_cua_driver_installer") as runner:
            assert tools_config.install_cua_driver(upgrade=True) is False
            runner.assert_not_called()


class TestCuaDriverInstallerSupplyChain:
    def test_installer_uses_pinned_release_asset_without_shell(self):
        from hermes_cli import tools_config

        with patch.object(tools_config, "_download_verified_cua_driver_asset") as download, \
             patch.object(tools_config, "_safe_extract_cua_driver_archive") as extract, \
             patch.object(tools_config, "_install_extracted_cua_driver", return_value=True) as install, \
             patch.object(tools_config, "_print_info"), \
             patch.object(tools_config.subprocess, "run") as run:
            assert tools_config._run_cua_driver_installer(label="Installing") is True

        release = download.call_args.args[0]
        assert release.version == "0.3.4"
        assert release.asset == "cua-driver-rs-0.3.4-darwin-universal.tar.gz"
        assert release.sha256 == "3332122c6d9e911d93ac10bdda4cc7ee8fbebbbd549dd6487f1c841cfef7d96e"
        assert "raw.githubusercontent.com" not in release.url
        assert "/main/" not in release.url
        extract.assert_called_once()
        install.assert_called_once()
        run.assert_not_called()

    def test_unknown_cua_driver_version_fails_closed(self):
        from hermes_cli import tools_config

        with patch.dict("os.environ", {"HERMES_CUA_DRIVER_VERSION": "9.9.9"}), \
             patch.object(tools_config, "_download_verified_cua_driver_asset") as download, \
             patch.object(tools_config, "_print_warning"):
            assert tools_config._run_cua_driver_installer(label="Installing") is False

        download.assert_not_called()

    def test_download_rejects_checksum_mismatch(self, tmp_path):
        from hermes_cli import tools_config

        class FakeResponse:
            def __init__(self, data: bytes):
                self._stream = io.BytesIO(data)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def geturl(self):
                return "https://github.com/trycua/cua/releases/download/cua-driver-rs-v0.3.4/test.tar.gz"

            def read(self, size=-1):
                return self._stream.read(size)

        release = tools_config.CuaDriverRelease(
            version="0.3.4",
            tag="cua-driver-rs-v0.3.4",
            asset="test.tar.gz",
            sha256="0" * 64,
        )
        destination = tmp_path / "test.tar.gz"

        with patch("urllib.request.urlopen", return_value=FakeResponse(b"not the expected bytes")):
            with pytest.raises(RuntimeError, match="checksum mismatch"):
                tools_config._download_verified_cua_driver_asset(release, destination)

        assert not destination.exists()

    def test_download_rejects_unexpected_redirect_host(self, tmp_path):
        from hermes_cli import tools_config

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def geturl(self):
                return "https://evil.example/test.tar.gz"

            def read(self, size=-1):
                return b""

        release = tools_config.CuaDriverRelease(
            version="0.3.4",
            tag="cua-driver-rs-v0.3.4",
            asset="test.tar.gz",
            sha256="0" * 64,
        )

        with patch("urllib.request.urlopen", return_value=FakeResponse()):
            with pytest.raises(RuntimeError, match="unexpected download host"):
                tools_config._download_verified_cua_driver_asset(release, tmp_path / "test.tar.gz")

    def test_safe_extract_rejects_tar_path_traversal(self, tmp_path):
        from hermes_cli import tools_config

        archive = tmp_path / "bad.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            data = b"owned"
            info = tarfile.TarInfo("../escape")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        with pytest.raises(RuntimeError, match="unsafe path"):
            tools_config._safe_extract_cua_driver_archive(archive, tmp_path / "extract")

    def test_runtime_install_hint_does_not_publish_curl_bash(self):
        from tools.computer_use.cua_backend import cua_driver_install_hint

        hint = cua_driver_install_hint()

        assert "raw.githubusercontent.com/trycua/cua/main" not in hint
        assert "curl" not in hint.lower()
        assert "hermes computer-use install" in hint
