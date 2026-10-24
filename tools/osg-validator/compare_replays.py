#!/usr/bin/env python3
"""Compare exact nullable per-answer verdicts without comparing feedback wording."""
import argparse
import hashlib
import json
from pathlib import Path

FIELDS = ['sap_syntax_pass', 'sap_activation_pass', 'unit_syntax_pass',
          'unit_pass', 'osg_stage', 'error_stage']

def compare(before, after):
    if set(before) != set(after):
        raise ValueError('Replay answer identifiers differ')
    ids = sorted(before)
    changes = {field: [ident for ident in ids if before[ident].get(field) != after[ident].get(field)]
               for field in FIELDS}
    def digest(values):
        vector = [[ident, [values[ident].get(field) for field in FIELDS]] for ident in ids]
        return hashlib.sha256(json.dumps(vector, separators=(',', ':')).encode()).hexdigest()
    return {'answers': len(ids), 'baseline': 'previous adapter recorded-answer replay',
            'fields': FIELDS,
            'answers_with_any_verdict_difference': len(set().union(*map(set, changes.values()))),
            'per_field_differences': {field: len(rows) for field, rows in changes.items()},
            'before_verdict_sha256': digest(before), 'after_verdict_sha256': digest(after),
            'examples': {field: rows[:20] for field, rows in changes.items() if rows}}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = compare(json.loads(args.before.read_text()), json.loads(args.after.read_text()))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return int(result['answers_with_any_verdict_difference'] != 0)

if __name__ == '__main__':
    raise SystemExit(main())
