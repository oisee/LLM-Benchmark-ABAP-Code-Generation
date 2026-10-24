"""Offline failure injection; never generates model answers."""
import asyncio
import json
import os
from pathlib import Path
import re
import signal
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / 'src'))
from osg_validator import OsgClient, evaluate

SOURCE = (ROOT / 'dataset/abap_canonical_solution/z_humaneval_000.abap').read_text()

class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import before mocking subprocess so availability probes are unaffected.
        import abap_interaction

    def test_compiler_crash_cleans_runtime_before_replacement(self):
        client = OsgClient(retries=0)
        mutant = re.sub(r'(METHOD has_close_elements\.)', r'\1 DO. ENDDO.', SOURCE, flags=re.I)
        outcomes = []
        def validate():
            try:
                outcomes.append(evaluate(client, '0.txt', mutant))
            except Exception as exc:
                outcomes.append(exc)
        thread = threading.Thread(target=validate)
        old_pid = None
        directories = []
        try:
            thread.start()
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if client.process:
                    old_pid = client.process.pid
                    directories = list((ROOT / 'tools/osg-validator/.runs').glob(f'answer-{old_pid}-*'))
                    if any((p / 'remaining.json').exists() for p in directories):
                        break
                time.sleep(.02)
            self.assertTrue(directories and (directories[0] / 'remaining.json').exists())
            os.kill(old_pid, signal.SIGKILL)
            # For this cleanup probe, disallow retrying the infinite answer.
            thread.join(15)
            self.assertFalse(thread.is_alive())
            self.assertTrue(evaluate(client, '0.txt', SOURCE)[0]['unit_pass'])
            live = []
            for proc in Path('/proc').iterdir():
                if proc.name.isdigit():
                    try:
                        fields = (proc / 'stat').read_text().split()
                        if int(fields[4]) == old_pid and fields[2] != 'Z':
                            live.append(proc.name)
                    except (OSError, ValueError):
                        pass
            self.assertEqual(live, [], 'old runtime process group survived replacement')
            self.assertFalse(any(p.exists() for p in directories), 'old scratch survived replacement')
        finally:
            client.close()
            if old_pid:
                try: os.killpg(old_pid, signal.SIGKILL)
                except ProcessLookupError: pass
            for p in directories: shutil.rmtree(p, ignore_errors=True)
            thread.join(95)

    def test_startup_failure_retries_bounded_without_verdict(self):
        from osg_validator import InfrastructureError
        client = OsgClient()
        try:
            with patch('osg_validator.subprocess.Popen', side_effect=OSError('startup unavailable')) as start:
                with self.assertRaisesRegex(InfrastructureError, 'after 3 attempts') as caught:
                    evaluate(client, '0.txt', SOURCE)
            self.assertEqual(start.call_count, 3)
            self.assertIsNone(caught.exception.tier['unit_pass'])
            self.assertEqual(caught.exception.tier['error_stage'], 'infra')
        finally:
            client.close()

    def test_eof_protocol_and_broken_pipe_retry_same_answer(self):
        from osg_validator import InfrastructureError
        import subprocess
        real_popen = subprocess.Popen
        for failure in ('eof', 'protocol', 'pipe', 'broken'):
            with self.subTest(failure=failure):
                client = OsgClient()
                attempts = []
                def start(*args, **kwargs):
                    # The first compiler fails; its replacement returns a valid verdict.
                    attempts.append(1)
                    if len(attempts) == 1:
                        script = "import sys; sys.stdin.readline(); "
                        if failure == 'protocol':
                            script += "print('not json', flush=True)"
                        else:
                            script += "print('{\"progress\":{\"syntax\":true,\"activation\":true}}', file=sys.stderr, flush=True)"
                    else:
                        script = "import sys,json; request=json.loads(sys.stdin.readline()); assert request['source']==" + repr(SOURCE) + "; print(json.dumps(dict(syntax=True,activation=True,unit_syntax=True,unit=True,stage=None)),flush=True)"
                    return real_popen([sys.executable, '-u', '-c', script], **kwargs)
                try:
                    with patch('osg_validator.subprocess.Popen', side_effect=start):
                        if failure == 'pipe':
                            client._start()
                            # Closing the input injects the same write failure as a lost pipe.
                            client.process.stdin.close()
                        if failure == 'broken':
                            client._start()
                            with patch.object(client.process.stdin, 'write', side_effect=BrokenPipeError('injected broken pipe')):
                                self.assertTrue(evaluate(client, '0.txt', SOURCE)[0]['unit_pass'])
                        else:
                            self.assertTrue(evaluate(client, '0.txt', SOURCE)[0]['unit_pass'])
                    self.assertEqual(len(attempts), 2)
                finally:
                    client.close()

    def test_persistent_crash_retains_known_stages(self):
        from osg_validator import InfrastructureError
        import subprocess
        real_popen = subprocess.Popen
        script = "import sys; sys.stdin.readline(); print('{\"progress\":{\"syntax\":true,\"activation\":true,\"unit_syntax\":true}}',file=sys.stderr,flush=True)"
        client = OsgClient()
        try:
            with patch('osg_validator.subprocess.Popen', side_effect=lambda *a, **kw: real_popen([sys.executable, '-u', '-c', script], **kw)) as start:
                with self.assertRaises(InfrastructureError) as caught:
                    evaluate(client, '0.txt', SOURCE)
            self.assertEqual(start.call_count, 3)
            tier = caught.exception.tier
            self.assertTrue(tier['sap_syntax_pass'])
            self.assertTrue(tier['sap_activation_pass'])
            self.assertTrue(tier['unit_syntax_pass'])
            self.assertIsNone(tier['unit_pass'])
        finally:
            client.close()

    def test_runner_checkpoints_without_feedback_or_next_request(self):
        from osg_validator import InfrastructureError
        from osg_run import run
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        name = 'osg-recovery-test-' + str(os.getpid())
        paths = [ROOT / 'data' / (name + suffix + '.json') for suffix in ('', '_tiers', '_manifest', '_result')]
        tier = dict(error_stage='infra', sap_syntax_pass=True, unit_pass=None)
        error = InfrastructureError('injected compiler failure')
        error.tier = tier
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json') as subset:
                json.dump({'tasks': ['0.txt']}, subset); subset.flush()
                args = SimpleNamespace(subset=subset.name,output_name=name,model='fake',base_url='http://127.0.0.1:1/v1',api_key='fake',max_tokens=50,repetitions=1,rounds=6)
                with patch('osg_run.ask_openai_direct', new_callable=AsyncMock, return_value=SOURCE) as ask, patch('osg_run.evaluate', side_effect=error):
                    with self.assertRaises(InfrastructureError):
                        asyncio.run(run(args))
                    self.assertEqual(ask.await_count, 1)
            chat = json.loads(paths[0].read_text())['0.txt'][0]
            self.assertEqual(chat[-1], {'role': 'assistant', 'content': SOURCE})
            self.assertEqual(len(chat), 3)
            tiers = json.loads(paths[1].read_text())['0.txt'][0]
            self.assertEqual(tiers, {'round_0': tier})
            from chat_state import chat_waiting_for_llm
            self.assertFalse(chat_waiting_for_llm(chat))
        finally:
            for p in paths: p.unlink(missing_ok=True)

    def test_benchmark_seam_uses_infra_retry_state(self):
        from osg_validator import InfrastructureError, run_single_prompt
        from chat_state import chat_has_infra_feedback, chat_waiting_for_llm
        error = InfrastructureError('injected')
        error.tier = {'error_stage': 'infra', 'unit_pass': None}
        chat = [{'role': 'assistant', 'content': SOURCE}]
        with patch('osg_validator.evaluate', side_effect=error):
            _, tiers = run_single_prompt(None, '0.txt', [chat], True, None)
        self.assertTrue(chat_has_infra_feedback(chat))
        self.assertFalse(chat_waiting_for_llm(chat))
        self.assertIsNone(tiers[0]['round_0']['unit_pass'])

    def test_verdict_fingerprint_covers_all_inputs(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('osg_audit', ROOT / 'tools/osg-validator/audit.py')
        audit = importlib.util.module_from_spec(spec); spec.loader.exec_module(audit)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for directory in ('tools/osg-validator', 'src', 'dataset/abap_tables'):
                (root / directory).mkdir(parents=True, exist_ok=True)
            for p in (ROOT / 'tools/osg-validator').glob('*.mjs'):
                shutil.copy2(p, root / 'tools/osg-validator' / p.name)
            shutil.copytree(ROOT / 'tools/osg-validator/compat', root / 'tools/osg-validator/compat')
            for relative in ('tools/osg-validator/package-lock.json', 'src/osg_validator.py', 'src/abap_interaction.py'):
                shutil.copy2(ROOT / relative, root / relative)
            for p in (ROOT / 'dataset/abap_tables').glob('*.abap'):
                shutil.copy2(p, root / 'dataset/abap_tables' / p.name)
            def fingerprint():
                with patch.object(audit, 'ROOT', root):
                    instance = audit.Audit(1, root / 'audit')
                    try: return instance.fingerprint
                    finally: instance.close()
            baseline = fingerprint()
            inputs = [root / 'src/abap_interaction.py', root / 'tools/osg-validator/package-lock.json', *sorted((root / 'dataset/abap_tables').glob('*.abap'))]
            self.assertEqual(len(inputs), 15)
            for p in inputs:
                original = p.read_bytes()
                try:
                    p.write_bytes(original + b'\nchanged')
                    self.assertNotEqual(fingerprint(), baseline, p.name)
                finally: p.write_bytes(original)

    def test_value_paths_and_truncation(self):
        import subprocess
        subprocess.run(['node', '--input-type=module', '-e', """
import assert from 'node:assert/strict';
import {abapValue} from './tools/osg-validator/feedback.mjs';
for (const v of ['https://example.invalid/a/b','/test/data/value','    at /test/data/value']) assert.equal(abapValue(v),v);
assert.match(abapValue('x'.repeat(3000)), /\\[truncated\\]$/);
"""], cwd=ROOT, check=True)

if __name__ == '__main__': unittest.main()
