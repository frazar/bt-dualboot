from pytest import fixture
import os
import shutil
from operator import itemgetter

from tests.helpers import pytest_unwrap
from tests_integration.helpers import cli_result, snapshot_cli_result
from tests.bt_windows.shared_fixtures import test_scheme, import_devices

from windows_registry import WindowsRegistry, WINDOWS10_REGISTRY_PATH


OPTS_WIN_MOUNT = ["--win", "/mnt/win"]
WIN_MOUNT_POINT = os.path.join(os.sep, "mnt", "win")
SYSTEM_REG = os.path.join(WIN_MOUNT_POINT, WINDOWS10_REGISTRY_PATH)
CLI_CONTEXT = "[env_single_windows] valid environment with single windows mounted"


def with_win(cmd_opts):
    return [*OPTS_WIN_MOUNT, *cmd_opts]


def filter_devices_macs(stdout, section_id):
    return [
        line.split(" ")[1] for line in stdout.split("\n") if line != "" and line.find(section_id) == 0
    ]


def snapshot_cli(*args, context=CLI_CONTEXT, **kwrd):
    return snapshot_cli_result(*args, context=context, **kwrd)


def snapshot_cli_win(snapshot, cmd_opts, *args, **kwrd):
    actual_opts = [*OPTS_WIN_MOUNT, *cmd_opts]
    return snapshot_cli(snapshot, actual_opts, *args, **kwrd)


@fixture(scope="module")
def tmpdir(tmp_path_factory):
    return tmp_path_factory.mktemp("windows_registry")


@fixture(scope="module")
def registry_file_path(tmpdir):
    backup_reg = str(tmpdir / "SYSTEM")
    shutil.copy(SYSTEM_REG, backup_reg)
    yield SYSTEM_REG
    shutil.move(backup_reg, SYSTEM_REG)


@fixture(scope="module")
def windows_registry(registry_file_path):
    return WindowsRegistry(registry_file_path)


@fixture(scope="module", autouse=True)
def import_windows_devices_once(windows_registry):
    do_import = pytest_unwrap(import_devices)
    scheme = pytest_unwrap(test_scheme)()

    do_import(windows_registry, scheme)


def test_no_args(snapshot):
    """should be identical to -h"""

    for res in snapshot_cli(snapshot, []):
        retcode, stdout = itemgetter("retcode", "stdout")(res)
        assert stdout.find("-h, --help") > 0
        assert retcode == 0


def test_no_args_but_win(snapshot):
    """should be error"""

    for res in snapshot_cli(snapshot, with_win([])):
        assert res["retcode"] == 2


def test_help(snapshot):
    snapshot_cli(snapshot, ["-h"])


# (user) $ -l
def test_list(snapshot):
    """should fail with error about permissions to /var/lib/bluetooth"""
    cmd_opts = ["-l"]
    for res in snapshot_cli_win(snapshot, cmd_opts):
        retcode, stderr = itemgetter("retcode", "stderr")(res)
        assert stderr.find("No Bluetooth devices found") > 0
        assert retcode == 1


# (root) # -l
def test_list_sudo(snapshot):
    """should list bluetooth devices"""
    cmd_opts = ["-l"]
    for res in snapshot_cli_win(snapshot, cmd_opts, sudo=True):
        retcode, stdout = itemgetter("retcode", "stdout")(res)
        assert stdout.find("Works both in Linux and Windows") > 0
        assert stdout.find("Needs sync") > 0
        assert stdout.find("Have to be paired in Windows") > 0
        assert retcode == 0


# (root) # -l --bot
def test_list_bot_sudo(snapshot):
    """should list bluetooth devices"""
    cmd_opts = ["-l", "--bot"]
    for res in snapshot_cli_win(snapshot, cmd_opts, sudo=True):
        assert res["retcode"] == 0


# (root) # -l == --list
def test_list_synonyms():
    headers = ["retcode", "stdout", "stderr"]
    # fmt: off
    res_l       = cli_result(with_win(["-l"]),        sudo=True)
    res_list    = cli_result(with_win(["--list"]),    sudo=True)
    # fmt: on

    assert res_list["retcode"] == 0, "retcode should be 0"

    for key in headers:
        assert res_l[key] == res_list[key], f"{headers[key]} expected to be the same"


class BaseTestSync:
    @fixture(autouse=True)
    def ensure_before_sync(self, tmpdir):
        backup_reg = str(tmpdir / "SYSTEM_before_TestSync")
        shutil.copy(SYSTEM_REG, backup_reg)

        self.assert_needs_sync_and_works(self.initial_needs_sync())

        yield
        shutil.move(backup_reg, SYSTEM_REG)

    @fixture
    def suite_snapshot(self, snapshot):
        """Append class name to snapshot path"""
        default_dir = snapshot.snapshot_dir
        test_name = default_dir.parts[-1]
        test_class = self.__class__.__name__
        snapshot.snapshot_dir = default_dir.parent / test_class / test_name
        return snapshot

    # @override
    def assert_after(self, expected_needs_sync):
        self.assert_needs_sync_and_works(expected_needs_sync)

    def assert_nothing_changed(self):
        self.assert_needs_sync_and_works(self.initial_needs_sync())

    def assert_needs_sync_and_works(self, expected_needs_sync):
        res = cli_result(self.build_opts(["-l", "--bot"]), sudo=True)
        stdout = res["stdout"]
        # fmt: off
        assert set(expected_needs_sync) == set(filter_devices_macs(stdout, "needs_sync"))
        assert set(expected_needs_sync) - set(filter_devices_macs(stdout, "works")) == set(expected_needs_sync),\
            "expected works section doesn't include devices needs sync"
        # fmt: on

    # @override
    def extra_opts(self):
        return []

    def build_opts(self, cmd_opts):
        return with_win([*cmd_opts, *self.extra_opts()])

    def initial_needs_sync(self):
        return [
            "B8:94:A5:FD:F1:0A",
            "C2:9E:1D:E2:3D:A5",
        ]


class DryRunMixin:
    # @override
    def extra_opts(self):
        return ["--dry-run"]

    # @override
    def assert_after(self, expected_needs_sync):
        self.assert_nothing_changed()


class TestSync(BaseTestSync):
    # --sync MAC    => After: One device to sync left
    def test_single_mac(self, suite_snapshot):
        cmd_opts = self.build_opts(["--sync", "C2:9E:1D:E2:3D:A5"])
        for res in snapshot_cli(suite_snapshot, cmd_opts, sudo=True):
            retcode, stdout = itemgetter("retcode", "stdout")(res)
            expected_output = "synced C2:9E:1D:E2:3D:A5 successfully"
            assert stdout.find(expected_output) >= 0
            assert retcode == 0

        self.assert_after(["B8:94:A5:FD:F1:0A"])

    # --sync MAC MAC    => After: No devices to sync
    def test_multipe_macs(self, suite_snapshot):
        cmd_opts = self.build_opts(["--sync", "C2:9E:1D:E2:3D:A5", "B8:94:A5:FD:F1:0A"])

        for res in snapshot_cli(suite_snapshot, cmd_opts, sudo=True):
            retcode, stdout = itemgetter("retcode", "stdout")(res)
            expected_output = "synced C2:9E:1D:E2:3D:A5, B8:94:A5:FD:F1:0A successfully"
            assert stdout.find(expected_output) >= 0
            assert retcode == 0

        self.assert_after(["NONE"])

    def test__when_no_devices__sync_single(self, suite_snapshot):
        # sync all devices => No devices to sync left
        cli_result(with_win(["--sync-all"]), sudo=True)

        cmd_opts = self.build_opts(["--sync", "C2:9E:1D:E2:3D:A5"])
        for res in snapshot_cli(suite_snapshot, cmd_opts, sudo=True):
            retcode, stderr = itemgetter("retcode", "stderr")(res)
            expected_error = "Can't push C2:9E:1D:E2:3D:A5! Not found or already in sync!"
            assert stderr.find(expected_error) >= 0
            assert retcode == 1

    # --sync WRONG_MAC              => Error
    def test_wrong_mac(self, suite_snapshot):
        cmd_opts = self.build_opts(["--sync", "F2:9E:1D:E2:3D:A5"])
        for res in snapshot_cli(suite_snapshot, cmd_opts, sudo=True):
            retcode, stderr = itemgetter("retcode", "stderr")(res)
            expected_error = "Can't push F2:9E:1D:E2:3D:A5! Not found"
            assert stderr.find(expected_error) >= 0
            assert retcode == 1

        self.assert_nothing_changed()

    # --sync WRONG_MAC VALID_MAC    => Error
    def test_valid_and_wrong_mac(self, suite_snapshot):
        cmd_opts = self.build_opts(["--sync", "C2:9E:1D:E2:3D:A5", "E8:94:A5:FD:F1:0A"])
        for res in snapshot_cli(suite_snapshot, cmd_opts, sudo=True):
            retcode, stderr = itemgetter("retcode", "stderr")(res)
            expected_error = "Can't push E8:94:A5:FD:F1:0A! Not found"
            assert stderr.find(expected_error) >= 0
            assert retcode == 1

        self.assert_nothing_changed()

    # --sync                        => Error
    def test_missing_mac(self, suite_snapshot):
        cmd_opts = self.build_opts(["--sync"])
        for res in snapshot_cli(suite_snapshot, cmd_opts, sudo=True):
            retcode, stderr = itemgetter("retcode", "stderr")(res)
            expected_error = "error: argument --sync: expected at least one argument"
            assert stderr.find(expected_error) >= 0
            assert retcode == 2

        self.assert_nothing_changed()


class TestSyncDryRun(DryRunMixin, TestSync):
    pass


class TestSyncAll(BaseTestSync):
    # --sync-all    => After: No devices to sync
    def test_sync_all(self, suite_snapshot):
        cmd_opts = self.build_opts(["--sync-all"])
        for res in snapshot_cli(suite_snapshot, cmd_opts, sudo=True):
            retcode, stdout = itemgetter("retcode", "stdout")(res)
            assert stdout.find("C2:9E:1D:E2:3D:A5") >= 0
            assert stdout.find("B8:94:A5:FD:F1:0A") >= 0
            assert stdout.find("done") >= 0
            assert retcode == 0

        self.assert_after(["NONE"])

    # --sync-all --bot    => After: No devices to sync
    def test_sync_all_bot(self, suite_snapshot):
        cmd_opts = self.build_opts(["--sync-all", "--bot"])
        for res in snapshot_cli(suite_snapshot, cmd_opts, sudo=True):
            retcode, stdout = itemgetter("retcode", "stdout")(res)
            assert stdout.find("C2:9E:1D:E2:3D:A5") >= 0
            assert stdout.find("B8:94:A5:FD:F1:0A") >= 0
            assert stdout.find("done") >= 0
            assert retcode == 0

        self.assert_after(["NONE"])

    # --sync-all    => After: No devices to sync
    def test__when_no_devices__sync_all(self, suite_snapshot):
        cmd_opts = self.build_opts(["--sync-all"])

        # sync all devices => No devices to sync left
        cli_result(cmd_opts, sudo=True)

        for res in snapshot_cli(suite_snapshot, cmd_opts, sudo=True):
            assert res["retcode"] == 0

        self.assert_after(["NONE"])

    # --sync-all --sync MAC     => Error
    def test_sync_all_and_sync(self, suite_snapshot):
        cmd_opts = self.build_opts(["--sync-all", "--sync", "123"])
        for res in snapshot_cli(suite_snapshot, cmd_opts, sudo=True):
            assert res["retcode"] == 2

        self.assert_nothing_changed()


class TestSyncAllDryRun(DryRunMixin, TestSyncAll):
    pass
