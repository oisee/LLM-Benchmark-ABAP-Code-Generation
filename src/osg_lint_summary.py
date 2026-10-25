#!/usr/bin/env python3
"""Summarize an OSG result file; scores count task/repetition conversations."""
import argparse
import json
from pathlib import Path


def summarize(results):
    runs = [rounds for repetitions in results.values() for rounds in repetitions]
    def rate(values):
        return sum(value is True for value in values) / len(runs) if runs else 0.0
    repaired = {'canonicalization': 0, 'syntax': 0}
    loops_used = 0
    for rounds in runs:
        for result in rounds.values():
            loops = result.get('lint_loops', [])
            loops_used += len(loops)
            for i, loop in enumerate(loops):
                if i + 1 == len(loops) and 'final_stage' not in result:
                    continue
                # A later stage proves this reject was cleared, even if unit tests fail.
                next_stage = loops[i + 1]['stage'] if i + 1 < len(loops) else result.get('final_stage')
                stage = loop['stage']
                if stage in repaired and next_stage != stage and (
                    stage == 'canonicalization' or next_stage != 'canonicalization'
                ):
                    repaired[stage] += 1
    return {'conversations': len(runs),
            'pass_at_1_without_lint': rate(r.get('round_0', {}).get('draft_unit_pass') for r in runs),
            'r0_after_lint': rate(r.get('round_0', {}).get('final_unit_pass') for r in runs),
            'best_r0_r5_with_lint': rate(any(r.get(f'round_{i}', {}).get('final_unit_pass') is True
                                            for i in range(6)) for r in runs),
            'lint_loops_used': loops_used, 'repaired_rejects': repaired}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', nargs='+', type=Path)
    args = parser.parse_args()
    print(json.dumps({p.stem: summarize(json.loads(p.read_text())) for p in args.results}, indent=2))


if __name__ == '__main__':
    main()
