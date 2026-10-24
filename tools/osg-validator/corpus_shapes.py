#!/usr/bin/env python3
"""Inventory every recorded user feedback message, normalizing only Element addresses."""
import ast
from collections import Counter, defaultdict
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]

def scan():
    data = json.loads((ROOT/'data/claude-opus-4-6.json').read_text())
    messages = Counter(); shapes = Counter(); kinds = Counter(); sizes = defaultdict(Counter)
    detail_templates = Counter(); examples = {}; titles = defaultdict(Counter); ordering = Counter(); parse_errors = []
    for task, chats in data.items():
        for rep, chat in enumerate(chats):
            for index, msg in enumerate(chat):
                if msg['role'] != 'user': continue
                text = msg['content']
                if not text.startswith('The '): continue
                prefix, _, body = text.partition('\n')
                messages[prefix] += 1
                if not body: continue
                body = body.split('\n Try to fix')[0].split('\nTry to fix')[0]
                body = re.sub(r"<Element 'stack' at 0x[0-9a-f]+>", repr('<Element stack>'), body)
                try: entries = ast.literal_eval(body)
                except (ValueError, SyntaxError):
                    parse_errors.append([task, rep, index]); continue
                sizes[prefix][len(entries)] += 1
                positions = []
                for entry in entries:
                    kind = entry.get('kind', entry.get('type', 'unknown'))
                    kinds[kind] += 1
                    stack = 'element' if isinstance(entry.get('stack'), str) else 'empty' if entry.get('stack') == [] else 'absent'
                    shape = (prefix, kind, entry.get('severity', ''), ','.join(entry), stack)
                    shapes[shape] += 1
                    examples.setdefault(kind, entry)
                    if 'title' in entry: titles[kind][entry['title']] += 1
                    if kind == 'failedAssertion':
                        detail = entry['details'][0]
                        family = ('table' if 'Table[' in detail else
                                  'character_prefix' if detail.startswith('Character string') else
                                  'scalar_values' if detail.startswith('Different values') else 'boolean')
                        detail_templates[family] += 1
                    if 'line' in entry: positions.append((entry['line'], entry.get('offset', 0)))
                if positions: ordering['source_order' if positions == sorted(positions) else 'not_source_order'] += 1
    return {'messages': dict(messages), 'kinds': dict(kinds), 'shapes': [dict(zip(['prefix','kind','severity','keys','stack','count'], (*k,v))) for k,v in shapes.items()],
            'entries_per_message': {k:dict(sorted(v.items())) for k,v in sizes.items()}, 'syntax_ordering':dict(ordering),
            'assertion_detail_families':dict(detail_templates), 'titles':{k:dict(v) for k,v in titles.items()}, 'examples':examples, 'parse_errors':parse_errors}

if __name__ == '__main__':
    result = scan()
    (ROOT/'tools/osg-validator/corpus-shapes.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('messages','kinds','shapes','syntax_ordering','parse_errors')}, indent=2))
