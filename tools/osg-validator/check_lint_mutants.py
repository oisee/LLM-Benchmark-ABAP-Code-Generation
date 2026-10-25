#!/usr/bin/env python3
"""Run passing controls, then show each lint check failing on a deliberate defect.

Run separately from other tests. Every changed source is restored in finally.
"""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CASES = [
    ('zero_prompt_changed', 'src/osg_run.py', "if lint_rounds else '')}", "if True else '')}",
     'test_zero_requests_identical_to_baseline'),
    ('repair_disabled', 'src/osg_run.py', 'for _ in range(lint_rounds):', 'for _ in range(0):',
     'test_pseudo_repaired'),
    ('budget_exceeded', 'src/osg_run.py', 'for _ in range(lint_rounds):', 'for _ in range(lint_rounds + 1):',
     'test_never_fixes_stops_at_budget'),
    ('retry_disabled', 'src/generate_llm_answers_openai_direct.py', 'max_retries = 5', 'max_retries = 1',
     'test_timeout_retry_in_lint_loop'),
    ('unit_stages_in_lint', 'tools/osg-validator/engine.mjs', 'if (input.lint_only === true) return result;', '',
     'test_lint_does_not_run_unit_stages'),
    ('round_feedback_missing', 'src/osg_run.py', "chat.append({'role':'user','content':feedback})", 'pass',
     'test_syntax_repair_and_multiple_rounds'),
]


def main():
    output = ROOT / 'osg-results/lint-mutations'
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for name, relative, old, new, test in CASES:
        command = [sys.executable, 'tools/osg-validator/test_lint.py', 'LintTests.' + test]
        control = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=180)
        (output / (name + '-control.log')).write_text(control.stdout + control.stderr)
        if control.returncode:
            raise RuntimeError('Control failed: ' + name)
        path = ROOT / relative
        original = path.read_text()
        assert old in original, name
        try:
            path.write_text(original.replace(old, new, 1))
            mutant = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=180)
            (output / (name + '.log')).write_text(mutant.stdout + mutant.stderr)
        finally:
            path.write_text(original)
        results.append({'mutation': name, 'control_passed': True, 'detected': mutant.returncode != 0})
        print(name, results[-1]['detected'], flush=True)
    (ROOT / 'tools/osg-validator/lint-mutation-summary.json').write_text(json.dumps(results, indent=2) + '\n')
    return 0 if all(result['detected'] for result in results) else 1


if __name__ == '__main__':
    sys.exit(main())
