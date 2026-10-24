#!/usr/bin/env python3
"""Inventory corpus API usage and prior SAP-accepted unresolved-name failures."""
import collections
import json
from pathlib import Path
import re
from audit import ROOT, STAGES, sap_from_feedback, cause, dump


def scan(replay_path):
    data=json.loads((ROOT/'data/claude-opus-4-6.json').read_text())
    tiers=json.loads((ROOT/'data/claude-opus-4-6_tiers.json').read_text())
    replay=json.loads(Path(replay_path).read_text())
    sources=[(str(p.relative_to(ROOT)),p.read_text()) for folder in ('abap_canonical_solution','abap_unittests') for p in sorted((ROOT/'dataset'/folder).glob('*.abap'))]
    missing={stage:collections.Counter() for stage in STAGES}
    examples={}
    missing_answers=collections.Counter()
    sql_evidence={t:[] for t in ('erp_006.txt','erp_011.txt','erp_014.txt')}
    for task,chats in data.items():
        for rep,chat in enumerate(chats):
            rnd=0
            for index,msg in enumerate(chat):
                if msg['role']!='assistant':continue
                ident=f'{task}:{rep}:R{rnd}'
                feedback=chat[index+1]['content'] if index+1<len(chat) else ''
                oracle=sap_from_feedback(feedback)
                saved=tiers.get(task,[])
                saved=saved[rep].get(f'round_{rnd}') if rep<len(saved) else None
                if saved:oracle.update({f:saved.get(f) for f in STAGES.values() if f!='unit_syntax_pass'})
                rnd+=1
                sources.append((ident,msg['content']))
                if task in sql_evidence and oracle.get('sap_syntax_pass'):
                    selects=re.findall(r'\bSELECT\b[\s\S]*?\.',msg['content'],re.I)
                    if any('@' not in select for select in selects):sql_evidence[task].append(ident)
                verdict=replay[ident]
                message=verdict.get('error_message') or ''
                names=re.findall(r'(?:Super class|Class|Function module|Method(?: definition)?|Type)\s+["\']?([\w~/]+)',message,re.I)
                for stage,field in STAGES.items():
                    if oracle.get(field) and not verdict.get(field):
                        if cause(message)=='missing library class/method/type or unresolved name':missing_answers[stage]+=1
                        for name in sorted(set(names) - {'not','definition'}):
                            missing[stage][name.upper()]+=1
                            examples.setdefault(name.upper(),ident)
    usage={}
    patterns={'classic_assert_methods':r'cl_aunit_assert\s*=>\s*(\w+)', 'classic_assert_inheritance':r'INHERITING\s+FROM\s+(cl_aunit_assert)', 'ddic_character_and_hash':r'\bTYPE\s+(char\d+|hash\d+)\b', 'function_modules':r"CALL\s+FUNCTION\s+'(\w+)'"}
    for kind,pattern in patterns.items():
        hits=collections.defaultdict(collections.Counter)
        for ident,source in sources:
            for name in re.findall(pattern,source,re.I):hits[name.upper()][ident]+=1
        usage[kind]={n:{'occurrences':sum(ids.values()),'sources':dict(ids)} for n,ids in sorted(hits.items())}
    core_names={p.name.lower() for p in (ROOT/'tools/osg-validator/.cache/open-abap-core/src').rglob('*') if p.is_file()}
    usage['missing_scanned_ddic']=[n for n in usage['ddic_character_and_hash'] if n.lower()+'.dtel.xml' not in core_names]
    dump(ROOT/'tools/osg-validator/compat-inventory.json',{'usage':usage,'prior_missing_library_false_reject_answers_by_stage':dict(missing_answers),'sap_accepted_unescaped_select_evidence':sql_evidence,'prior_false_reject_names_by_stage':{s:dict(c.most_common()) for s,c in missing.items()},'example_answers':examples,'note':'Unresolved methods and local TY_* names may be dataset contract mismatches, not missing SAP APIs. No such local names are supplied.'})

if __name__=='__main__':
    import sys
    scan(sys.argv[1] if len(sys.argv)>1 else ROOT/'osg-results/replay.json')
