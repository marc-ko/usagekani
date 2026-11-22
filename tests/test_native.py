import subprocess
import unittest
from pathlib import Path


class NativeTests(unittest.TestCase):
    def test_real_firmware_parser_and_fragmented_at_frames(self):
        root = Path(__file__).resolve().parents[1]
        build = root / '.cache'
        build.mkdir(exist_ok=True)
        binary = build / 'native_usage_tests.exe'
        subprocess.run(['gcc', '-std=c99', '-Wall', '-Wextra', '-Werror',
                        '-Ihal_firmware/Core/Inc', 'tests/native_usage_tests.c',
                        'hal_firmware/Core/Src/usage_data.c', '-o', str(binary)], cwd=root, check=True)
        subprocess.run([str(binary), str(root / 'tests/fixtures/status.json')], cwd=root, check=True)
