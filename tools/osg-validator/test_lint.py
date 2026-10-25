"""Real compiler and local scripted HTTP model; no external model calls."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / 'src'))
import osg_run
from osg_validator import OsgClient, evaluate
from osg_lint_summary import summarize

SOURCE = (ROOT / 'dataset/abap_canonical_solution/z_humaneval_000.abap').read_text()
PSEUDO = SOURCE.replace('ENDCLASS.', 'END CLASS.').replace('PUBLIC SECTION.', 'PUBLIC SECTION')
SYNTAX = SOURCE.replace('ENDMETHOD.', 'invalid ??? . ENDMETHOD.', 1)


class LintTests(unittest.TestCase):
    def execute(self, answers, lint_rounds=0, rounds=1, runner=None, timeout=False):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                requests.append(raw)
                index = len(requests) - 1
                if timeout and index == 1:
                    time.sleep(.5)
                    return
                answer = answers[min(index, len(answers) - 1)]
                data = json.dumps({'id': 'fake', 'object': 'chat.completion', 'created': 0,
                    'model': 'fake', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': answer},
                    'finish_reason': 'length' if index else 'stop'}]}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer((str(IPv4Address(2130706433)), 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        async def fast_retry(_):
            pass
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / 'data').mkdir()
                (root / 'dataset').symlink_to(ROOT / 'dataset', target_is_directory=True)
                subset = root / 'subset.json'
                subset.write_text(json.dumps({'tasks': ['0.txt']}))
                args = SimpleNamespace(subset=subset, output_name='fake', model='fake',
                    base_url=f'http://{server.server_address[0]}:{server.server_port}/v1', api_key='fake',
                    max_tokens=5000, repetitions=1, rounds=rounds, lint_rounds=lint_rounds,
                    request_timeout=.2 if timeout else 600)
                target = runner or osg_run.run
                with patch.dict(target.__globals__, ROOT=root), patch('generate_llm_answers_openai_direct.asyncio.sleep', fast_retry):
                    asyncio.run(target(args))
                values = [json.loads((root / 'data' / f'fake{suffix}.json').read_text())
                          for suffix in ('', '_tiers', '_result', '_manifest')]
                return requests, *values
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_zero_requests_identical_to_baseline(self):
        source = subprocess.check_output(['git', 'show', '99ee876:src/osg_run.py'], text=True)
        namespace = {'__name__': 'baseline_runner'}
        exec(compile(source, 'baseline_runner', 'exec'), namespace)
        before = self.execute([SYNTAX, SOURCE], rounds=2, runner=namespace['run'])[0]
        after = self.execute([SYNTAX, SOURCE], rounds=2)[0]
        self.assertEqual(before, after)
        self.assertEqual(len(after), 2)

    def test_pseudo_repaired(self):
        requests, chats, tiers, results, manifest = self.execute([PSEUDO, SOURCE], lint_rounds=3)
        tier = tiers['0.txt'][0]['round_0']
        result = results['0.txt'][0]['round_0']
        self.assertFalse(tier['draft_verdict']['unit_pass'])
        self.assertTrue(tier['unit_pass'])
        self.assertEqual(tier['lint_loops_used'], 1)
        self.assertEqual(result['lint_loops'][0]['stage'], 'canonicalization')
        self.assertTrue(result['lint_loops'][0]['truncated'])
        feedback = json.loads(requests[1])['messages'][-1]['content']
        self.assertTrue(feedback.startswith('Code structure issue:'))
        self.assertNotIn('unit test', feedback.lower())
        self.assertEqual(len(chats['0.txt'][0]), 4)
        self.assertEqual(manifest['lint_instruction'], osg_run.LINT_INSTRUCTION)
        self.assertEqual(manifest['maximum_requests'], 4)
        self.assertEqual(summarize(results), dict(conversations=1, pass_at_1_without_lint=0,
            r0_after_lint=1, best_r0_r5_with_lint=1, lint_loops_used=1,
            repaired_rejects={'canonicalization': 1, 'syntax': 0}))

    def test_never_fixes_stops_at_budget(self):
        requests, _, tiers, results, _ = self.execute([SYNTAX], lint_rounds=2)
        self.assertEqual(len(requests), 3)
        self.assertEqual(tiers['0.txt'][0]['round_0']['lint_loops_used'], 2)
        self.assertFalse(results['0.txt'][0]['round_0']['final_unit_pass'])
        self.assertEqual(summarize(results)['repaired_rejects']['syntax'], 0)
        self.assertTrue(json.loads(requests[1])['messages'][-1]['content'].startswith('The syntax check failed'))

    def test_timeout_retry_in_lint_loop(self):
        requests, _, tiers, results, _ = self.execute([PSEUDO, SOURCE, SOURCE], lint_rounds=1, timeout=True)
        self.assertEqual(len(requests), 3)
        self.assertEqual(requests[1], requests[2])
        self.assertTrue(tiers['0.txt'][0]['round_0']['unit_pass'])
        self.assertEqual(results['0.txt'][0]['round_0']['lint_loops_used'], 1)

    def test_summary_does_not_infer_repairs_from_incomplete_rounds(self):
        rounds = {'round_0': {'draft_unit_pass': False, 'lint_loops': [{'stage': 'syntax'}]}}
        score = summarize({'0.txt': [rounds]})
        self.assertEqual(score['repaired_rejects']['syntax'], 0)
        self.assertEqual(score['pass_at_1_without_lint'], 0)
        rounds['round_5'] = {'draft_unit_pass': True, 'final_unit_pass': True, 'final_stage': None}
        self.assertEqual(summarize({'0.txt': [rounds]})['best_r0_r5_with_lint'], 1)

    def test_lint_does_not_run_unit_stages(self):
        client = OsgClient()
        try:
            tier, feedback = evaluate(client, '0.txt', SOURCE, lint_only=True)
            self.assertTrue(tier['sap_activation_pass'])
            self.assertIsNone(tier['unit_syntax_pass'])
            self.assertIsNone(tier['unit_pass'])
            self.assertEqual(tier['test_count'], 0)
            self.assertEqual(feedback, 'The activation was successful.')
            self.assertTrue(evaluate(client, '0.txt', SOURCE)[0]['unit_pass'])
        finally:
            client.close()

    def test_syntax_repair_and_multiple_rounds(self):
        wrong = SOURCE.replace('abap_true', 'abap_false')
        requests, _, tiers, results, _ = self.execute([SYNTAX, wrong, SOURCE], lint_rounds=2, rounds=2)
        self.assertEqual(len(requests), 3)
        self.assertEqual(len(tiers['0.txt'][0]), 2)
        self.assertFalse(results['0.txt'][0]['round_0']['final_unit_pass'])
        self.assertTrue(results['0.txt'][0]['round_1']['final_unit_pass'])
        self.assertEqual(results['0.txt'][0]['round_1']['lint_loops_used'], 0)
        self.assertIn('The unit test failed', json.loads(requests[2])['messages'][-1]['content'])
        self.assertEqual(summarize(results)['repaired_rejects']['syntax'], 1)


if __name__ == '__main__':
    unittest.main()
