# SPDX-License-Identifier: Apache-2.0
"""验证启动入口的帮助、交互保护与路径处理，不运行安装或真实云阶段。"""

from pathlib import Path
import shutil
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
