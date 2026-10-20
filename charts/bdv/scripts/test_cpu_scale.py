"""Offline isolation and seed-contract checks; no cluster changes."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import yaml

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
spec=importlib.util.spec_from_file_location('cpu_scale',HERE/'test-cpu-scale.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class CPUAcceptanceTests(unittest.TestCase):
    def test_live_profile_refused(self):
        v={'ranger':{'authorization':{'serviceName':'bdv-test-trino'}},
           'ingress':{'bdvHost':'bdv-test.dinecodetalkers.com'},
           'objectStorage':{'bucket':'bdv-test-warehouse'},
           'bdv':{'autoscaling':{'enabled':True,'minReplicas':2,'maxReplicas':6,
                               'targetCPUUtilizationPercentage':70}}}
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'values.yaml'; path.write_text(yaml.safe_dump(v))
            module.profile(path)
            v['ingress']['bdvHost']='bdv.dinecodetalkers.com'
            path.write_text(yaml.safe_dump(v))
            with self.assertRaises(ValueError): module.profile(path)

    def test_scoped_grants_and_seed(self):
        grants=module.grants(module.USER)
        for g in grants:
            self.assertEqual(g['resources']['table'],[module.TABLE])
            self.assertEqual(g['users'],[module.USER])
        self.assertEqual(grants[1]['accesses'],['select'])
        self.assertNotIn('IF NOT EXISTS',module.SEED)
        self.assertNotIn('repeat(',module.SEED)
        self.assertIn('concat(digest,digest,digest,digest)',module.SEED)

    def test_undeploy_refuses_wrong_namespace_label(self):
        with tempfile.TemporaryDirectory() as temp:
            fake=Path(temp)/'kubectl'
            fake.write_text('#!/bin/sh\nprintf false\n')
            fake.chmod(0o755)
            # Helm must never be invoked after the namespace guard fails.
            helm=Path(temp)/'helm'
            helm.write_text('#!/bin/sh\necho UNEXPECTED_HELM_INVOCATION\nexit 99\n')
            helm.chmod(0o755)
            r=subprocess.run(['bash',str(HERE/'undeploy-test.sh'),'--execute'],
                env={**os.environ,'PATH':temp+os.pathsep+os.environ['PATH']},
                capture_output=True,text=True)
            self.assertNotEqual(r.returncode,0)
            self.assertIn('expected bdv-test-isolated',r.stdout)
            self.assertNotIn('UNEXPECTED_HELM',r.stdout)

if __name__=='__main__': unittest.main()
