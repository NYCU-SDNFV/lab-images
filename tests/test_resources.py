import contextlib
import importlib.util
import inspect
import io
from pathlib import Path
import sys
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "base"))
import lab_resources as resources
import patch_mininet as patcher


class MemorySysctl:
    def __init__(self, values):
        self.values = dict(values)
        self.events = []
        self.read_errors = {}
        self.write_errors = {}
        self.ignore_writes = set()

    @property
    def writes(self):
        return [event[1:] for event in self.events if event[0] == "write"]

    def read(self, name):
        self.events.append(("read", name))
        if name in self.read_errors:
            raise self.read_errors[name]
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name]

    def write(self, name, value):
        self.events.append(("write", name, value))
        if name in self.write_errors:
            raise self.write_errors[name]
        if name not in self.ignore_writes:
            self.values[name] = value


class MemoryLimits:
    RLIM_INFINITY = -1
    RLIMIT_NPROC = "RLIMIT_NPROC"
    RLIMIT_NOFILE = "RLIMIT_NOFILE"

    def __init__(self, **values):
        self.values = {
            self.RLIMIT_NPROC: (-1, -1),
            self.RLIMIT_NOFILE: (65536, 65536),
        }
        self.values.update(values)
        self.calls = []
        self.errors = {}
        self.ignore_writes = False

    def getrlimit(self, name):
        return self.values[name]

    def setrlimit(self, name, value):
        self.calls.append((name, value))
        if name in self.errors:
            raise self.errors[name]
        if not self.ignore_writes:
            self.values[name] = value


def host_values():
    return {name: str(value) for name, value in resources.HOST_MINIMUMS.items()}


def container_values():
    values = {
        name: str(value) for name, value in resources.CONTAINER_HOST_MINIMUMS.items()
    }
    values.update({
        "net.ipv4.tcp_rmem": "4096 131072 6291456",
        "net.ipv4.tcp_wmem": "4096 16384 4194304",
    })
    return values


class HostTests(unittest.TestCase):
    def test_prepare_preflights_every_knob_then_raises_only_lower_values(self):
        values = {
            name: str(value - 1) for name, value in resources.HOST_MINIMUMS.items()
        }
        values["net.core.rmem_max"] = "134217728"
        sysctl = MemorySysctl(values)
        result = resources.prepare_host(sysctl)
        for name, minimum in resources.HOST_MINIMUMS.items():
            self.assertEqual(result[name], max(int(values[name]), minimum))
        self.assertNotIn("net.core.rmem_max", [name for name, _ in sysctl.writes])
        first_write = next(
            i for i, event in enumerate(sysctl.events) if event[0] == "write"
        )
        self.assertEqual(
            {event[1] for event in sysctl.events[:first_write]},
            set(resources.HOST_MINIMUMS),
        )
        resources.verify_host(sysctl)

    def test_all_required_missing_knobs_fail_before_any_writes(self):
        for missing in resources.HOST_MINIMUMS.keys() - resources.OPTIONAL_LEGACY.keys():
            with self.subTest(missing=missing):
                values = host_values()
                values["fs.file-max"] = "1"
                del values[missing]
                sysctl = MemorySysctl(values)
                with self.assertRaises(resources.ResourceError) as caught:
                    resources.prepare_host(sysctl)
                self.assertIn(missing, str(caught.exception))
                self.assertIn("Docker ENGINE host", str(caught.exception))
                self.assertEqual(sysctl.writes, [])

    def test_invalid_or_unreadable_last_knob_prevents_all_host_writes(self):
        for problem in ("not-an-integer", "1 2", "-1", PermissionError("denied")):
            with self.subTest(problem=problem):
                sysctl = MemorySysctl(host_values())
                sysctl.values["fs.file-max"] = "1"
                name = "kernel.pty.max"
                if isinstance(problem, Exception):
                    sysctl.read_errors[name] = problem
                else:
                    sysctl.values[name] = problem
                with self.assertRaisesRegex(resources.ResourceError, name):
                    resources.prepare_host(sysctl)
                self.assertEqual(sysctl.writes, [])

    def test_only_absent_legacy_route_knob_is_optional_and_is_reported(self):
        sysctl = MemorySysctl(host_values())
        name = "net.ipv4.route.max_size"
        del sysctl.values[name]
        self.assertIsNone(resources.prepare_host(sysctl)[name])
        self.assertIsNone(resources.verify_host(sysctl)[name])
        self.assertIn(f"# {name}: unavailable; obsolete", resources.host_config(sysctl))
        self.assertEqual(sysctl.writes, [])
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(resources, "Sysctl", return_value=sysctl):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                resources.main(["host-verify", "--profile", "course"])
        self.assertIn(f"{name}: unavailable legacy knob", errors.getvalue())
        self.assertIn("Course host prerequisites verified", output.getvalue())

    def test_legacy_permission_and_parse_failures_are_not_ignored(self):
        name = "net.ipv4.route.max_size"
        for problem in (PermissionError("denied"), "invalid"):
            with self.subTest(problem=problem):
                sysctl = MemorySysctl(host_values())
                sysctl.values["fs.file-max"] = "1"
                if isinstance(problem, Exception):
                    sysctl.read_errors[name] = problem
                else:
                    sysctl.values[name] = problem
                with self.assertRaisesRegex(resources.ResourceError, name):
                    resources.prepare_host(sysctl)
                self.assertEqual(sysctl.writes, [])

    def test_verify_is_read_only_and_rejects_16_mib_buffers(self):
        sysctl = MemorySysctl(host_values())
        for name in ("net.core.rmem_max", "net.core.wmem_max"):
            sysctl.values[name] = "16777216"
        with self.assertRaises(resources.ResourceError) as caught:
            resources.verify_host(sysctl)
        self.assertIn("net.core.rmem_max=16777216", str(caught.exception))
        self.assertIn("net.core.wmem_max=16777216", str(caught.exception))
        self.assertIn("67108864", str(caught.exception))
        self.assertEqual(sysctl.writes, [])

    def test_config_preserves_larger_values_and_does_not_write(self):
        sysctl = MemorySysctl(host_values())
        sysctl.values["fs.file-max"] = "9223372036854775807"
        sysctl.values["net.core.rmem_max"] = "134217728"
        sysctl.values["net.core.wmem_max"] = "212992"
        config = resources.host_config(sysctl)
        self.assertIn("fs.file-max = 9223372036854775807", config)
        self.assertIn("net.core.rmem_max = 134217728", config)
        self.assertIn("net.core.wmem_max = 67108864", config)
        self.assertEqual(sysctl.writes, [])

    def test_prepare_rechecks_values_before_writing(self):
        sysctl = MemorySysctl(host_values())
        name = "fs.file-max"
        reads = iter(("1", "20000"))
        original_read = sysctl.read

        def read(setting):
            if setting == name:
                sysctl.values[name] = next(reads)
            return original_read(setting)

        sysctl.read = read
        result = resources.prepare_host(sysctl)
        self.assertEqual(result[name], 20000)
        self.assertEqual(sysctl.writes, [])

    def test_denied_host_write_is_actionable(self):
        sysctl = MemorySysctl(host_values())
        name = "net.core.wmem_max"
        sysctl.values[name] = "212992"
        sysctl.write_errors[name] = PermissionError("read-only or denied")
        with self.assertRaises(resources.ResourceError) as caught:
            resources.prepare_host(sysctl)
        self.assertIn(f"{name}: cannot write 67108864", str(caught.exception))
        self.assertIn("--privileged --network host", str(caught.exception))

    def test_failed_readback_is_not_reported_as_success(self):
        sysctl = MemorySysctl(host_values())
        sysctl.values["fs.file-max"] = "1"
        sysctl.ignore_writes.add("fs.file-max")
        with self.assertRaisesRegex(resources.ResourceError, "fs.file-max: read back"):
            resources.prepare_host(sysctl)

    def test_cli_failures_are_nonzero_and_do_not_emit_success_or_partial_config(self):
        for action in ("host-verify", "host-config", "host-prepare"):
            with self.subTest(action=action):
                sysctl = MemorySysctl(host_values())
                del sysctl.values["kernel.pty.max"]
                output, errors = io.StringIO(), io.StringIO()
                with mock.patch.object(resources, "Sysctl", return_value=sysctl):
                    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                        with self.assertRaises(SystemExit) as caught:
                            resources.main([action, "--profile", "course"])
                self.assertEqual(caught.exception.code, 1)
                self.assertEqual(output.getvalue(), "")
                self.assertIn("kernel.pty.max", errors.getvalue())
                self.assertEqual(sysctl.writes, [])

    def test_host_cli_requires_explicit_action_and_course_profile(self):
        for argv in ([], ["host-prepare"], ["host-prepare", "--profile", "mininet"]):
            with self.subTest(argv=argv):
                with mock.patch.object(resources, "Sysctl") as constructor:
                    with contextlib.redirect_stderr(io.StringIO()):
                        with self.assertRaises(SystemExit) as caught:
                            resources.main(argv)
                self.assertEqual(caught.exception.code, 2)
                constructor.assert_not_called()


class ContainerTests(unittest.TestCase):
    def test_initializes_tcp_without_accessing_invisible_host_knobs(self):
        sysctl = MemorySysctl(container_values())
        limits = MemoryLimits()
        self.assertIsNone(resources.initialize_container(sysctl, limits))
        self.assertEqual(sysctl.values["net.ipv4.tcp_rmem"], "10240 131072 16777216")
        self.assertEqual(sysctl.values["net.ipv4.tcp_wmem"], "10240 87380 16777216")
        accessed = {event[1] for event in sysctl.events}
        self.assertEqual(
            accessed, resources.CONTAINER_HOST_MINIMUMS.keys() | resources.TCP_MINIMUMS.keys()
        )
        self.assertEqual({name for name, _ in sysctl.writes}, set(resources.TCP_MINIMUMS))
        self.assertEqual(limits.calls, [])
        self.assertEqual(limits.values["RLIMIT_NPROC"], (-1, -1))
        self.assertEqual(limits.values["RLIMIT_NOFILE"], (65536, 65536))

    def test_raises_small_finite_soft_and_hard_limits(self):
        limits = MemoryLimits(RLIMIT_NPROC=(1024, 2048), RLIMIT_NOFILE=(4096, 8192))
        resources.initialize_container(MemorySysctl(container_values()), limits)
        self.assertEqual(limits.values["RLIMIT_NPROC"], (8192, 8192))
        self.assertEqual(limits.values["RLIMIT_NOFILE"], (16384, 16384))

    def test_preserves_unlimited_soft_and_hard_limits(self):
        for name, minimum in resources.PROCESS_MINIMUMS.items():
            for initial, expected in (
                ((-1, -1), (-1, -1)),
                ((1024, -1), (minimum, -1)),
                ((minimum * 2, -1), (minimum * 2, -1)),
                ((minimum * 2, minimum * 4), (minimum * 2, minimum * 4)),
                ((1024, minimum * 4), (minimum, minimum * 4)),
            ):
                with self.subTest(name=name, initial=initial):
                    limits = MemoryLimits(**{name: initial})
                    resources.initialize_container(MemorySysctl(container_values()), limits)
                    self.assertEqual(limits.values[name], expected)

    def test_larger_tcp_tunables_are_never_lowered(self):
        values = container_values()
        for name in resources.TCP_MINIMUMS:
            values[name] = "32768 262144 134217728"
        sysctl = MemorySysctl(values)
        resources.initialize_container(sysctl, MemoryLimits())
        self.assertEqual(sysctl.values, values)
        self.assertEqual(sysctl.writes, [])

    def test_low_visible_host_prerequisites_fail_before_any_local_changes(self):
        for name in resources.CONTAINER_HOST_MINIMUMS:
            with self.subTest(name=name):
                sysctl = MemorySysctl(container_values())
                sysctl.values[name] = str(resources.CONTAINER_HOST_MINIMUMS[name] - 1)
                limits = MemoryLimits(RLIMIT_NPROC=(1024, 1024))
                with self.assertRaises(resources.ResourceError) as caught:
                    resources.initialize_container(sysctl, limits)
                self.assertIn(name, str(caught.exception))
                self.assertIn("host-prepare --profile course", str(caught.exception))
                self.assertEqual(sysctl.writes, [])
                self.assertEqual(limits.calls, [])

    def test_16_mib_or_default_host_buffers_do_not_satisfy_course_profile(self):
        for name in ("net.core.wmem_max", "net.core.rmem_max"):
            for value in ("212992", "16777216"):
                with self.subTest(name=name, value=value):
                    sysctl = MemorySysctl(container_values())
                    sysctl.values[name] = value
                    with self.assertRaises(resources.ResourceError) as caught:
                        resources.initialize_container(sysctl, MemoryLimits())
                    self.assertIn(f"{name}={value}", str(caught.exception))
                    self.assertIn("67108864", str(caught.exception))
                    self.assertEqual(sysctl.writes, [])

    def test_missing_visible_prerequisites_are_not_silently_skipped(self):
        for name in resources.CONTAINER_HOST_MINIMUMS:
            with self.subTest(name=name):
                sysctl = MemorySysctl(container_values())
                del sysctl.values[name]
                limits = MemoryLimits(RLIMIT_NPROC=(1024, 1024))
                with self.assertRaisesRegex(resources.ResourceError, name):
                    resources.initialize_container(sysctl, limits)
                self.assertEqual(sysctl.writes, [])
                self.assertEqual(limits.calls, [])

    def test_denied_local_writes_identify_each_tcp_parameter(self):
        for name in resources.TCP_MINIMUMS:
            with self.subTest(name=name):
                sysctl = MemorySysctl(container_values())
                sysctl.write_errors[name] = PermissionError("denied")
                with self.assertRaises(resources.ResourceError) as caught:
                    resources.initialize_container(sysctl, MemoryLimits())
                self.assertIn(f"{name}: cannot write", str(caught.exception))
                self.assertIn("network-isolated", str(caught.exception))

    def test_missing_or_malformed_tcp_settings_are_errors(self):
        name = "net.ipv4.tcp_rmem"
        for value in (None, "1 2", "1 2 3 4", "no", "-1 2 3"):
            with self.subTest(value=value):
                sysctl = MemorySysctl(container_values())
                if value is None:
                    del sysctl.values[name]
                else:
                    sysctl.values[name] = value
                with self.assertRaisesRegex(resources.ResourceError, name):
                    resources.initialize_container(sysctl, MemoryLimits())

    def test_local_readback_failure_is_not_silenced(self):
        sysctl = MemorySysctl(container_values())
        sysctl.ignore_writes.add("net.ipv4.tcp_rmem")
        with self.assertRaisesRegex(resources.ResourceError, "tcp_rmem: read back"):
            resources.initialize_container(sysctl, MemoryLimits())

    def test_denied_process_limit_adjustment_names_the_limit(self):
        for name in resources.PROCESS_MINIMUMS:
            with self.subTest(name=name):
                limits = MemoryLimits(**{name: (1024, 1024)})
                limits.errors[name] = PermissionError("not allowed")
                sysctl = MemorySysctl(container_values())
                with self.assertRaises(resources.ResourceError) as caught:
                    resources.initialize_container(sysctl, limits)
                self.assertIn(name, str(caught.exception))
                self.assertIn("--ulimit", str(caught.exception))
                self.assertEqual(sysctl.writes, [])

    def test_process_limit_readback_failure_is_not_silenced(self):
        limits = MemoryLimits(RLIMIT_NPROC=(1024, 1024))
        limits.ignore_writes = True
        with self.assertRaisesRegex(resources.ResourceError, "RLIMIT_NPROC: read back"):
            resources.initialize_container(MemorySysctl(container_values()), limits)


class PatchTests(unittest.TestCase):
    def test_only_fixlimits_is_replaced_and_api_is_stable(self):
        before = "# preserve prefix\nsentinel = 7\n"
        after = "\n# preserve suffix\ndef unrelated():\n    return sentinel\n"
        source = before + patcher.EXPECTED_FIX_LIMITS.lstrip("\n") + after
        patched = patcher.patch_source(source)
        self.assertEqual(patched, before + patcher.REPLACEMENT_FIX_LIMITS + after)
        namespace = {"debug": mock.Mock()}
        exec(compile(patched, "<test-mininet-util>", "exec"), namespace)
        self.assertEqual(str(inspect.signature(namespace["fixLimits"])), "()")
        with mock.patch.object(resources, "initialize_container") as initialize:
            self.assertIsNone(namespace["fixLimits"]())
        initialize.assert_called_once_with()
        self.assertEqual(namespace["unrelated"](), 7)

    def test_patch_does_not_hide_initializer_failure(self):
        namespace = {"debug": mock.Mock()}
        exec(patcher.patch_source(patcher.EXPECTED_FIX_LIMITS), namespace)
        error = resources.ResourceError("net.core.wmem_max is too low")
        with mock.patch.object(resources, "initialize_container", side_effect=error):
            with self.assertRaises(resources.ResourceError) as caught:
                namespace["fixLimits"]()
        self.assertIs(caught.exception, error)

    def test_formatting_and_comments_do_not_require_an_upstream_rebase(self):
        source = patcher.EXPECTED_FIX_LIMITS.replace(
            "def fixLimits():", "def fixLimits( ):"
        ).replace("    try:", "    # upstream comment\n    try:")
        self.assertIn("from lab_resources import initialize_container", patcher.patch_source(source))

    def test_unexpected_upstream_structure_fails_closed(self):
        original = patcher.EXPECTED_FIX_LIMITS
        variants = (
            "",
            original + original,
            original.replace("def fixLimits():", "def fixLimits(extra=None):"),
            original.replace("8192", "8193", 1),
            original.replace("    try:", "    extra_call()\n    try:"),
            original.replace("except Exception:", "except OSError:"),
            original.replace("def fixLimits():", "@decorator\ndef fixLimits():"),
            original.replace("def fixLimits():", "async def fixLimits():"),
            patcher.REPLACEMENT_FIX_LIMITS,
        )
        for source in variants:
            with self.subTest(source=source[:80]):
                with self.assertRaisesRegex(ValueError, "Refusing to patch"):
                    patcher.patch_source(source)


@unittest.skipUnless(sys.platform.startswith("linux"), "requires a Linux lab-base image")
class InstalledMininetTests(unittest.TestCase):
    def test_installed_mininet_fixlimits_delegates_and_propagates_errors(self):
        if importlib.util.find_spec("mininet") is None:
            self.skipTest("run inside the built lab-base image")
        from mininet.util import fixLimits

        with mock.patch.object(resources, "initialize_container") as initialize:
            self.assertIsNone(fixLimits())
        initialize.assert_called_once_with()
        with mock.patch.object(
            resources, "initialize_container",
            side_effect=resources.ResourceError("test resource failure"),
        ):
            with self.assertRaisesRegex(resources.ResourceError, "test resource failure"):
                fixLimits()


if __name__ == "__main__":
    unittest.main()
