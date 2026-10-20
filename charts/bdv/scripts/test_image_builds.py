import hashlib
import importlib.util
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('jdbc',ROOT/'scripts/verify-jdbc.py')
jdbc=importlib.util.module_from_spec(spec);spec.loader.exec_module(jdbc)


class ImageBuildTests(unittest.TestCase):
    def test_build_plan_has_separate_parent_and_runtime_tags(self):
        result=subprocess.run(['bash',str(ROOT/'scripts/build-images.sh'),'--plan'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn(':jdbc-9.7.0-capture-0.1.34',result.stdout)
        self.assertIn(':runtime-capture-0.1.34',result.stdout)
        self.assertIn('PLAN ONLY',result.stdout)

    def fixture(self,folder,major):
        path=Path(folder)/'fixture.jar'
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('com/mysql/cj/jdbc/Driver.class',struct.pack('>IHH',0xCAFEBABE,0,major))
        return path

    def test_java8_driver_header_and_checksum(self):
        with tempfile.TemporaryDirectory() as folder:
            path=self.fixture(folder,52)
            with patch.object(jdbc,'EXPECTED',hashlib.sha256(path.read_bytes()).hexdigest()):
                self.assertEqual(jdbc.verify(path),(0,52))

    def test_newer_java_class_is_rejected_even_when_hash_matches(self):
        with tempfile.TemporaryDirectory() as folder:
            path=self.fixture(folder,53)
            with patch.object(jdbc,'EXPECTED',hashlib.sha256(path.read_bytes()).hexdigest()):
                with self.assertRaisesRegex(ValueError,'Java 8'):jdbc.verify(path)

    def test_unexpected_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,'checksum'):jdbc.verify(self.fixture(folder,52))


if __name__=='__main__':unittest.main()
