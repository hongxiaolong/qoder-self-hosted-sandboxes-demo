# SPDX-License-Identifier: Apache-2.0
"""验证启动入口的帮助、交互保护与路径处理，不运行安装或真实云阶段。"""

from pathlib import Path
import shutil
import re
import select
import subprocess
import tempfile
import unittest


class StartScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qoder wizard ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = Path(__file__).resolve().parents[1] / "start.sh"
        self.script = self.root / "start.sh"
        shutil.copy2(source, self.script)

    def test_bash_syntax_is_valid(self):
        result = subprocess.run(
            ["bash", "-n", str(self.script)], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_enter_continues_template_and_runtime_stages_instead_of_exiting(self):
        source = self.script.read_text()
        functions = "\n".join(re.findall(r"^(?:pause|confirm|confirm_action)\(\) \{.*?^\}", source, re.M | re.S))
        for message in ["现在准备仓库和两个 Sandbox 模板", "现在创建本轮云端运行资源", "回收这轮已有 Qoder/Sandbox 资源并重建"]:
            with self.subTest(message=message):
                action = next(line for line in source.splitlines() if message in line)
                result = subprocess.run(["/bin/bash", "-uc", 'DIM= RESET= YELLOW=;\n' + functions + '\n' + action + '\nprintf "CONTINUED\\n"'], input="\n", text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("CONTINUED", result.stdout)
                self.assertIn("Ctrl+C", result.stdout)

    def test_running_wizard_uses_loaded_body_if_script_is_updated(self):
        gate = 'printf "READY\\n"\nIFS= read -r _\n' + '# read-ahead padding\n' * 1000
        source = self.script.read_text().replace('if [[ "$#" -gt 0 ]]; then', gate + 'if [[ "$#" -gt 0 ]]; then', 1)
        self.script.write_text(source)
        process = subprocess.Popen(["/bin/bash", str(self.script), "--help"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def close_process():
            if process.poll() is None:
                process.kill()
            process.communicate()
        self.addCleanup(close_process)
        self.assertTrue(select.select([process.stdout], [], [], 5)[0], "wizard did not reach pause")
        self.assertEqual(process.stdout.readline().strip(), "READY")
        self.script.write_text("# updated while running\n" * 500 + source)
        stdout, stderr = process.communicate("\n", timeout=5)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("六阶段", stdout)

    def test_chinese_punctuation_does_not_extend_bash_variable_name(self):
        warning = next(line for line in self.script.read_text().splitlines() if '旧运行状态：' in line)
        result = subprocess.run(
            ['/bin/bash', '-uc', 'warn() { printf "%s\\n" "$1"; }; existing_status=partial;\n' + warning],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('partial，无法直接续接', result.stdout)

    def test_help_works_from_another_directory_without_mutation(self):
        result = subprocess.run(
            ["bash", str(self.script), "--help"], cwd=self.root.parent,
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("六阶段", result.stdout)
        self.assertIn("./start.sh", result.stdout)
        self.assertEqual(list(self.root.iterdir()), [self.script])

    def test_noninteractive_execution_cannot_install_or_create_resources(self):
        result = subprocess.run(
            ["bash", str(self.script)], cwd=self.root.parent,
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("交互式终端", result.stderr)
        self.assertEqual(list(self.root.iterdir()), [self.script])

    def test_preview_explains_all_stages_without_files_or_credentials(self):
        result = subprocess.run(["/bin/bash", str(self.script), "--preview"], cwd=self.root.parent,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count(" / 6 · "), 6)
        for word in ["AK/SK", "Qoder", "Team", "ACR", "Python", "101", "不含表头", "不创建资源"]:
            self.assertIn(word, result.stdout)
        self.assertEqual(list(self.root.iterdir()), [self.script])

    def test_unknown_option_fails_before_any_setup(self):
        result = subprocess.run(
            ["bash", str(self.script), "--unknown"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(list(self.root.iterdir()), [self.script])
