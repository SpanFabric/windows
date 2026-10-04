"""Executable regressions for the shared native-Windows verification boundary."""
import importlib.util
import os
import pathlib
import re
import subprocess
import tempfile
import textwrap
import unittest
import types
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "bridge_boundary_subject", REPO / "tests/verification/test_verify_review_state.py"
)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def job(document, name):
    match = re.search(
        r"(?ms)^  " + re.escape(name) + r":\n(.*?)(?=^  [A-Za-z][\w-]*:\n|\Z)",
        document,
    )
    if not match:
        raise AssertionError("missing job " + name)
    return match.group(1)


def assertion_program(document):
    gate = job(document, "verification-gate")
    if not re.search(r"(?m)^    needs: windows-trusted-runner$", gate):
        raise AssertionError("Windows dependency missing")
    if not re.search(r"(?m)^    if: \$\{\{ always\(\) \}\}$", gate):
        raise AssertionError("gate must execute unconditionally after dependency")
    steps = gate.split("    steps:\n", 1)[1]
    first = steps.split("      - name: ", 2)
    if len(first) < 3 or first[0].strip():
        raise AssertionError("assertion must precede checkout")
    assertion = first[1]
    if not re.search(r"(?m)^        shell: bash$", assertion):
        raise AssertionError("assertion must use Bash")
    if not re.search(
        r"(?m)^          WINDOWS_TRUSTED_RUNNER_RESULT: "
        r"\$\{\{ needs.windows-trusted-runner.result \}\}$", assertion
    ):
        raise AssertionError("assertion must consume the actual needs result as data")
    match = re.search(r"(?ms)^        run: \|\n(.*)\Z", assertion)
    if not match:
        raise AssertionError("assertion program missing")
    if not first[2].startswith("Checkout\n"):
        raise AssertionError("checkout must follow the result assertion")
    if "continue-on-error:" in gate or re.search(r"(?m)^        if:", assertion):
        raise AssertionError("prerequisite assertion must not be skipped or tolerated")
    return textwrap.dedent(match.group(1))


class WindowsBoundaryTests(unittest.TestCase):
    def helper(self):
        return bridge.BridgeTests()

    def test_wsl_and_arbitrary_path_hosts_rejected_without_fixed_git(self):
        for value in (
            r"C:\WINDOWS\system32\bash.exe",
            r"c:\windows\SYSTEM32\BASH.EXE",
            r"C:\Windows\System32\..\System32\bash.exe",
            r"C:\Tools\bash.exe",
            r"C:\msys64\usr\bin\bash.exe",
            r"C:\cygwin64\bin\bash.exe",
        ):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):
                    self.helper().select_bash(True, value, lambda p: p == value,
                                              is_suitable=lambda p: False)

    def test_fixed_git_order_and_non_windows_path(self):
        helper = self.helper()
        fixed = [r"C:\Program Files\Git\bin\bash.exe",
                 r"C:\Program Files\Git\usr\bin\bash.exe"]
        self.assertEqual(fixed[0], helper.select_bash(True, fixed[1], lambda p: p in fixed))
        self.assertEqual(fixed[1], helper.select_bash(True, None, lambda p: p == fixed[1]))
        self.assertEqual("/usr/bin/bash", helper.select_bash(False, "/usr/bin/bash",
                                                           lambda p: p == "/usr/bin/bash"))

    def test_windows_never_looks_up_path_before_host_selection(self):
        with mock.patch.object(bridge.shutil, "which", side_effect=AssertionError("PATH queried")):
            with mock.patch.object(bridge, "os", types.SimpleNamespace(name="nt")):
                # Host discovery tests model installation availability explicitly:
                # Linux CI has no native Windows Program Files installation.
                with mock.patch.object(bridge.pathlib.Path, "is_file", return_value=True):
                    self.assertEqual(r"C:\Program Files\Git\bin\bash.exe",
                                     self.helper().bash())

    def test_positive_path_fallback_and_failed_suitability(self):
        helper = self.helper()
        candidate = r"D:\Native Git\bin\bash.exe"
        for suitable in (True, False):
            probe = mock.Mock(return_value=suitable)
            resolver = mock.Mock(return_value=candidate)
            if suitable:
                self.assertEqual(candidate, helper.select_bash(
                    True, resolver, lambda p: p == candidate, probe))
            else:
                with self.assertRaises(RuntimeError):
                    helper.select_bash(True, resolver, lambda p: p == candidate, probe)
            resolver.assert_called_once_with()
            probe.assert_called_once_with(candidate)

    def test_fixed_hosts_prevent_path_lookup_and_probe(self):
        helper = self.helper()
        fixed = [r"C:\Program Files\Git\bin\bash.exe",
                 r"C:\Program Files\Git\usr\bin\bash.exe"]
        for available in (fixed, fixed[1:]):
            resolver = mock.Mock(side_effect=AssertionError("PATH must not override fixed host"))
            probe = mock.Mock(side_effect=AssertionError("fixed host must not use fallback probe"))
            self.assertEqual(available[0], helper.select_bash(
                True, resolver, lambda p: p in available, probe))
            resolver.assert_not_called()
            probe.assert_not_called()

    def test_missing_fallback_is_not_probed(self):
        probe = mock.Mock(side_effect=AssertionError("missing executable must not run"))
        with self.assertRaises(RuntimeError):
            self.helper().select_bash(True, r"D:\Missing\bash.exe", lambda p: False, probe)
        probe.assert_not_called()

    def test_known_wsl_is_rejected_even_if_injected_probe_claims_success(self):
        for candidate in (r"C:\Windows\System32\bash.exe",
                          r"c:\WINDOWS\system32\..\system32\BASH.EXE",
                          r"C:\Windows\Sysnative\wsl.exe"):
            probe = mock.Mock(return_value=True)
            with self.assertRaises(RuntimeError):
                self.helper().select_bash(True, candidate, lambda p: p == candidate, probe)
            probe.assert_not_called()

    def test_non_windows_path_does_not_probe_native_suitability(self):
        probe = mock.Mock(side_effect=AssertionError("native probe on non-Windows"))
        self.assertEqual("/opt/bash", self.helper().select_bash(
            False, "/opt/bash", lambda p: p == "/opt/bash", probe))
        probe.assert_not_called()

    def test_windows_path_discovery_only_after_both_fixed_hosts_absent(self):
        candidate = r"D:\Native Git\bin\bash.exe"
        with mock.patch.object(bridge, "os", types.SimpleNamespace(
                name="nt", environ=os.environ)):
            with mock.patch.object(bridge.pathlib.Path, "is_file",
                                   autospec=True,
                                   side_effect=lambda p: str(p) == candidate):
                with mock.patch.object(bridge.shutil, "which", return_value=candidate) as lookup:
                    with mock.patch.object(bridge.BridgeTests, "windows_bash_suitable",
                                           return_value=True) as probe:
                        self.assertEqual(candidate, self.helper().bash())
                        lookup.assert_called_once_with("bash")
                        probe.assert_called_once_with(candidate)

    def test_probe_requires_success_and_exact_complete_marker(self):
        candidate = r"D:\Native Git\bin\bash.exe"
        for code, output in ((0, "SPAN_GPU_NATIVE_MSYS_BASH_OK\n"),
                             (1, "SPAN_GPU_NATIVE_MSYS_BASH_OK\n"),
                             (0, ""), (0, "bash.exe\n"), (0, "Linux\n"),
                             (0, "SPAN_GPU_NATIVE_MSYS_BASH_OK"),
                             (0, "noise\nSPAN_GPU_NATIVE_MSYS_BASH_OK\n")):
            with mock.patch.object(bridge.subprocess, "run",
                    return_value=types.SimpleNamespace(returncode=code, stdout=output)):
                self.assertEqual(code == 0 and output == "SPAN_GPU_NATIVE_MSYS_BASH_OK\n",
                                 self.helper().windows_bash_suitable(candidate))

    def test_real_unrelated_executable_cannot_qualify_as_bash(self):
        self.assertFalse(self.helper().windows_bash_suitable(bridge.sys.executable))

    @unittest.skipIf(os.name == "nt", "native Linux/Unix rejection is exercised on non-Windows CI")
    def test_real_non_windows_bash_fails_native_suitability(self):
        self.assertFalse(self.helper().windows_bash_suitable(self.helper().bash()))

    def test_probe_start_failure_and_timeout_fail_closed(self):
        candidate = r"D:\Native Git\bin\bash.exe"
        for error in (OSError("cannot start"),
                      subprocess.TimeoutExpired(candidate, 10),
                      UnicodeError("invalid output")):
            with mock.patch.object(bridge.subprocess, "run", side_effect=error):
                self.assertFalse(self.helper().windows_bash_suitable(candidate))

    def test_probe_is_static_bounded_and_isolates_startup_and_git_environment(self):
        candidate = r"D:\Native Git\path'name\bash.exe"
        with mock.patch.dict(bridge.os.environ, {
                "BASH_ENV": "hostile-startup", "ENV": "hostile-startup",
                "GIT_EXEC_PATH": "hostile-git", "GIT_CONFIG_COUNT": "1"}):
            with mock.patch.object(bridge.subprocess, "run", return_value=types.SimpleNamespace(
                    returncode=0, stdout="SPAN_GPU_NATIVE_MSYS_BASH_OK\n")) as run:
                self.assertTrue(self.helper().windows_bash_suitable(candidate))
                args, kwargs = run.call_args
                self.assertEqual(candidate, args[0][0])
                self.assertNotIn(candidate, args[0][-1])
                for key in ("BASH_ENV", "ENV", "GIT_EXEC_PATH", "GIT_CONFIG_COUNT"):
                    self.assertNotIn(key, kwargs["env"])
                self.assertEqual(10, kwargs["timeout"])
                self.assertEqual(tempfile.gettempdir(), kwargs["cwd"])
                self.assertIn('MINGW*|MSYS*', args[0][-1])
                self.assertIn('cygpath -u -- "$SPAN_GPU_BASH_PROBE_NATIVE"', args[0][-1])
                self.assertIn('git --version', args[0][-1])
                self.assertIn('[[ "$native" == "$SPAN_GPU_BASH_PROBE_NATIVE" ]]', args[0][-1])

    @unittest.skipUnless(os.name == "nt", "real native Windows nonstandard Git installation path")
    def test_real_nonstandard_git_path_fallback_passes_positive_probe(self):
        # System-temp junction aliases the installed runtime, not a repository
        # copy. Only fixed-candidate availability is simulated; probe and path
        # conversion execute the real native host with no suitability injection.
        installed = pathlib.Path(r"C:\Program Files\Git")
        self.assertTrue((installed / "bin/bash.exe").is_file(),
                        "native positive proof requires installed Git Bash")
        with tempfile.TemporaryDirectory(prefix="spangpu-positive-bash-") as tmp:
            alias = pathlib.Path(tmp) / "Nonstandard Git installation"
            candidate = str(alias / "bin/bash.exe")
            try:
                created = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                     "$ErrorActionPreference='Stop'; New-Item -ItemType Junction "
                     "-Path $env:SPAN_GPU_PROBE_ALIAS -Target $env:SPAN_GPU_PROBE_TARGET | Out-Null"],
                    env=dict(os.environ, SPAN_GPU_PROBE_ALIAS=str(alias),
                             SPAN_GPU_PROBE_TARGET=str(installed)),
                    capture_output=True, text=True)
                self.assertEqual(0, created.returncode, created.stdout + created.stderr)
                selected = self.helper().select_bash(
                    True, candidate, lambda p: p == candidate and pathlib.Path(p).is_file())
                self.assertEqual(candidate, selected)
                self.assertEqual("/c/path'name/runner",
                                 self.helper().bash_path(selected, r"C:\path'name\runner"))
            finally:
                # Native rmdir unlinks the junction itself, never its target.
                if os.path.lexists(alias):
                    os.rmdir(alias)

    def test_workflow_prerequisite_result_matrix(self):
        workflow = (REPO / ".github/workflows/verification-gate.yml").read_text()
        program = assertion_program(workflow)
        bash = self.helper().bash()
        for result in ("success", "failure", "cancelled", "skipped",
                       "timeout", "unknown", "", "SUCCESS", "success "):
            with self.subTest(result=result):
                env = dict(os.environ, WINDOWS_TRUSTED_RUNNER_RESULT=result)
                completed = subprocess.run([bash, "--noprofile", "--norc", "-c", program],
                                           env=env, capture_output=True, text=True)
                self.assertEqual(result == "success", completed.returncode == 0,
                                 completed.stdout + completed.stderr)
                self.assertIn("WINDOWS_TRUSTED_RUNNER_RESULT=" + result, completed.stdout)

    def test_workflow_contract_rejects_skipping_or_tolerating_assertion(self):
        source = (REPO / ".github/workflows/verification-gate.yml").read_text()
        variants = [
            source.replace("if: $" + "{{ always() }}",
                           "if: $" + "{{ always() && needs.windows-trusted-runner.result == 'success' }}"),
            source.replace("needs: windows-trusted-runner", "needs: some-other-job"),
            source.replace("      - name: Assert native Windows prerequisite\n",
                           "      - name: Assert native Windows prerequisite\n        continue-on-error: true\n"),
            source.replace("      - name: Assert native Windows prerequisite\n",
                           "      - name: Assert native Windows prerequisite\n        if: $" + "{{ success() }}\n"),
        ]
        for document in variants:
            with self.subTest(document=document):
                with self.assertRaises(AssertionError):
                    assertion_program(document)

    def test_workflow_success_keeps_immutable_runner_and_tests(self):
        workflow = (REPO / ".github/workflows/verification-gate.yml").read_text()
        gate = job(workflow, "verification-gate")
        assertion_program(workflow)
        self.assertIn("git cat-file blob HEAD:scripts/trusted_verify_review_state.sh", gate)
        self.assertIn('"$trusted_runner" --repo-root "$GITHUB_WORKSPACE" --base "$BASE_SHA"', gate)
        self.assertIn("python -m unittest discover -s tests/verification", gate)
        windows = job(workflow, "windows-trusted-runner")
        self.assertIn("WINDOWS_GIT_BASH_PATH=", windows)
        self.assertNotIn("Get-Command bash", windows)
        self.assertNotIn("Invoke-Expression", windows)

    @unittest.skipUnless(os.name == "nt", "real native Windows path transport")
    def test_apostrophe_native_conversion_is_lossless(self):
        helper = self.helper()
        converted = helper.bash_path(helper.bash(), r"C:\path'name\runner")
        self.assertEqual("/c/path'name/runner", converted)

    @unittest.skipUnless(os.name == "nt", "real native Windows filesystem objects")
    def test_native_path_character_matrix_and_non_injection(self):
        helper = self.helper()
        bash = helper.bash()
        values = ["path'name", "path''name", "path name", "path&name",
                  "path(name)", "path$name", "path;name", "path[name]",
                  "path\N{LATIN SMALL LETTER U WITH DIAERESIS}name", "path\N{CJK UNIFIED IDEOGRAPH-8DEF}name",
                  "path\N{GRAVE ACCENT}name", "path$(touch WTR_SENTINEL)"]
        with tempfile.TemporaryDirectory(prefix="spangpu-wtr-path-") as tmp:
            root = pathlib.Path(tmp)
            for leaf in values:
                with self.subTest(leaf=leaf):
                    target = root / leaf / "runner"
                    target.parent.mkdir()
                    target.write_text("same-object", encoding="utf-8")
                    env = dict(os.environ, SPAN_GPU_NATIVE_PATH=str(target))
                    echoed = subprocess.run(
                        [bash, "--noprofile", "--norc", "-c",
                         'printf "%s" "$SPAN_GPU_NATIVE_PATH"'],
                        env=env, cwd=root, capture_output=True, encoding="utf-8", check=True,
                    )
                    self.assertEqual(str(target), echoed.stdout)
                    converted = helper.bash_path(bash, target)
                    read = subprocess.run(
                        [bash, "--noprofile", "--norc", "-c", 'cat -- "$SPAN_GPU_POSIX_PATH"'],
                        env=dict(os.environ, SPAN_GPU_POSIX_PATH=converted),
                        cwd=root, capture_output=True, text=True, check=True,
                    )
                    self.assertEqual("same-object", read.stdout)
                    self.assertFalse((root / "WTR_SENTINEL").exists())

    @unittest.skipUnless(os.name == "nt", "native trailing whitespace")
    def test_native_conversion_preserves_trailing_space(self):
        helper = self.helper()
        self.assertEqual("/c/path name/runner ",
                         helper.bash_path(helper.bash(), "C:\\path name\\runner "))

    @unittest.skipUnless(os.name == "nt", "Windows invalid control characters")
    def test_invalid_control_characters_fail_cleanly(self):
        for value in ("C:\\bad\0path", "C:\\bad\npath", "C:\\bad\rpath"):
            with self.assertRaises(ValueError):
                self.helper().bash_path(self.helper().bash(), value)


if __name__ == "__main__":
    unittest.main()
