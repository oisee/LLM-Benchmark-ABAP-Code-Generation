#!/usr/bin/env python3
"""Offline, resumable canonical/mutation gate and recorded-answer calibration."""
import argparse
import collections
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import itertools
import json
import os
from pathlib import Path
import random
import re
import sqlite3
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / 'src'))
from osg_validator import OsgClient, evaluate

STAGES = {'syntax': 'sap_syntax_pass', 'activation': 'sap_activation_pass',
          'unit_syntax': 'unit_syntax_pass', 'unit': 'unit_pass'}
LOCAL = threading.local()
CLIENTS = []


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + '\n')


def task_name(path):
    n = path.stem.removeprefix('z_humaneval_')
    return (n if n.startswith('erp_') else str(int(n))) + '.txt'


class Audit:
    def __init__(self, workers, output):
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.output = output
        output.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(output / 'answers.sqlite', timeout=120)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS answers (key TEXT PRIMARY KEY, value TEXT)')
        engine_files = ('bootstrap.mjs', 'ddl.mjs', 'engine.mjs', 'worker.mjs')
        engine = b''.join((ROOT / 'tools/osg-validator' / name).read_bytes() for name in engine_files)
        engine += b''.join(p.read_bytes() for p in sorted((ROOT / 'tools/osg-validator/compat').glob('*')) if p.is_file())
        engine += (ROOT / 'src/osg_validator.py').read_bytes()
        self.fingerprint = hashlib.sha256(engine).hexdigest()
        self.requests = self.cached = 0

    def key(self, task, source):
        test = ROOT / 'dataset/abap_unittests' / (class_name(task) + '_test.abap')
        return hashlib.sha256((self.fingerprint + task + source + test.read_text()).encode()).hexdigest()

    def run(self, items):
        """Items are (id, task, source). Cache deduplicates identical answers."""
        known, pending = {}, {}
        for ident, task, source in items:
            key = self.key(task, source)
            row = self.db.execute('SELECT value FROM answers WHERE key=?', (key,)).fetchone()
            if row:
                known[key] = json.loads(row[0]); self.cached += 1
            else:
                pending[key] = (task, source)
        def check(item):
            key, (task, source) = item
            if not hasattr(LOCAL, 'client'):
                LOCAL.client = OsgClient(); CLIENTS.append(LOCAL.client)
            tier, _ = evaluate(LOCAL.client, task, source)
            return key, tier
        start = time.monotonic()
        for i, (key, tier) in enumerate(self.pool.map(check, pending.items()), 1):
            known[key] = tier
            self.db.execute('INSERT OR REPLACE INTO answers VALUES (?, ?)', (key, json.dumps(tier)))
            self.db.commit()
            self.requests += 1
            if i % 100 == 0:
                print(f'{i}/{len(pending)} new answers, {i / (time.monotonic()-start):.2f}/s', flush=True)
        return {ident: known[self.key(task, source)] for ident, task, source in items}

    def close(self):
        self.pool.shutdown()
        for client in CLIENTS:
            client.close()
        self.db.close()


def class_name(task):
    n = task.removesuffix('.txt')
    return 'z_humaneval_' + (n if n.startswith('erp_') else n.zfill(3))


def code_mask(source):
    """Keep code offsets while hiding comments and quoted literal contents."""
    chars=list(source);i=0
    while i<len(chars):
        c=source[i]
        if c=='"' or (c=='*' and (i==0 or source[i-1]=='\n')):
            end=source.find('\n',i)
            if end<0:end=len(chars)
            chars[i:end]=' '*(end-i);i=end;continue
        if c in ("'",'`','|'):
            quote=c;end=i+1
            while end<len(chars):
                if source[end]==quote:
                    if end+1<len(chars) and source[end+1]==quote and quote!='|':end+=2;continue
                    end+=1;break
                end+=1
            for j in range(i,end):
                if chars[j]!='\n':chars[j]=' '
            i=end;continue
        i+=1
    return ''.join(chars)


def mutants(task, source):
    from abap_interaction import extract_contract
    mask=code_mask(source)
    method=extract_contract(task)['method_name']
    body=re.search(r'(?im)^\s*METHOD\s+'+re.escape(method)+r'\s*\.([\s\S]*?)^\s*ENDMETHOD\s*\.',mask)
    if body is None:raise ValueError('Canonical entry method not found: '+task)
    offset=body.start(1);bodymask=mask[body.start(1):body.end(1)]
    yield 'syntax_break', 'syntax', source.replace('ENDMETHOD.', 'invalid ??? . ENDMETHOD.', 1)
    yield 'unknown_type', 'syntax', source.replace('ENDMETHOD.', 'DATA bad TYPE z_osg_missing_type. ENDMETHOD.', 1)
    # Flip the outer comparison, not an assignment inside a functional argument
    # or table expression, and never alter comments or quoted strings.
    flipped=False
    for statement in re.finditer(r'(?im)^\s*(?:IF|ELSEIF|CHECK|WHILE)\b[^.]*\.',bodymask):
        depth=0;i=statement.start()+offset
        while i<statement.end()+offset:
            c=mask[i]
            if c in '([':depth+=1
            elif c in ')]':depth-=1
            elif depth==0 and c in '=<>':
                if (c=='=' and mask[i:i+2]=='=>') or (c=='>' and mask[i-1:i] in ('-','=')):
                    i+=1;continue
                op=mask[i:i+2] if mask[i:i+2] in ('<=','>=','<>') else c
                mapping={'<':'>=', '>':'<=', '<=':'>', '>=':'<', '=':'<>', '<>':'='}
                yield 'flipped_comparison','unit',source[:i]+mapping[op]+source[i+len(op):]
                flipped=True;break
            i+=1
        if flipped:break
    match = re.search(r'(?im)\bDO\s+([^\n.]+?)\s+TIMES\.', bodymask)
    if match:
        a,b=match.span();a+=offset;b+=offset
        yield 'off_by_one', 'unit', source[:a]+'DATA(osg_mutant_bound) = '+match[1]+' + 1. DO osg_mutant_bound TIMES.'+source[b:]
    result = re.search(r'RETURNING\s+VALUE\(\s*(\w+)\s*\)', source, re.I)
    if result:
        # Return the initial value from the tested public entry method.
        yield 'result_not_assigned', 'unit', re.sub(r'(?im)^(\s*METHOD\s+'+re.escape(method)+r'\s*\.)', rf'\1\n    CLEAR {result[1]}. RETURN.', source, count=1)
    if task.startswith('erp_'):
        match = re.search(r'(?im)\b(?:FROM|INTO|UPDATE|MODIFY)\s+(z\w+)\b', mask)
        if match:
            a,b=match.span(1)
            yield 'wrong_table', 'syntax', source[:a]+'z_osg_missing_table'+source[b:]
        # A known field in a declared DDIC row becomes an unknown component.
        match = re.search(r'(?i)\b\w+-(employee_id|country_code|mat_id|zname|zrev|holiday_date|customer_id|from_step|to_step)\b', mask)
        if match:
            a,b=match.span(1)
            yield 'wrong_field', 'syntax', source[:a]+'osg_missing_field'+source[b:]


def gate(audit):
    sources = {task_name(p):p.read_text() for p in sorted((ROOT/'dataset/abap_canonical_solution').glob('*.abap'))}
    initial_requests=audit.requests
    started=time.monotonic()
    verdicts=audit.run([(t,t,s) for t,s in sources.items()])
    excluded=[{'task':t,'stage':v.get('osg_stage') or v['error_stage'],'message':v['error_message']} for t,v in verdicts.items() if not v['unit_pass']]
    for item in excluded:
        if item['task'] in ('erp_012.txt','erp_013.txt','erp_014.txt','erp_015.txt'):
            item['reason']='needs the SAP flight data model, not supplied by the benchmark'
        elif item['task']=='erp_005.txt':
            item['reason']='dataset defect: canonical references ZFS_DATA, but dataset supplies ZFNET_DATA'
        elif item['task']=='162.txt':
            item['reason']='legacy hash API contract not established by public SAP documentation: encoding, HASH160 output and error behavior'
    dump(ROOT/'tools/osg-validator/excluded.json',excluded)
    eligible={t:s for t,s in sources.items() if verdicts[t]['unit_pass']}
    cases=[]
    for t,s in eligible.items():
        for kind,stage,mutant in mutants(t,s):
            if mutant != s:
                cases.append((f'{t}:{kind}',t,mutant,kind,stage))
    killed=audit.run([c[:3] for c in cases])
    kinds={}
    survivors=[]
    weaknesses=[]
    for ident,task,_,kind,stage in cases:
        v=killed[ident]; actual=v.get('osg_stage') or v['error_stage']
        if v['unit_pass'] and task in ('21.txt','34.txt') and kind in ('flipped_comparison','result_not_assigned'):
            weaknesses.append({'task':task,'kind':kind,'reason':'No cardinality assertion; empty result accepted' if task=='21.txt' else 'No assertion in unit test'})
            continue
        d=kinds.setdefault(kind,{'total':0,'killed':0,'expected_stage':stage,'expected_stage_hits':0})
        d['total']+=1;d['killed']+=not bool(v['unit_pass']);d['expected_stage_hits']+=actual==stage
        if actual!=stage:
            survivors.append({'task':task,'kind':kind,'expected':stage,'actual':actual,'message':(v.get('error_message') or '')[:400]})
    total=sum(x['total'] for x in kinds.values());dead=sum(x['killed'] for x in kinds.values())
    red=len(excluded)>len(sources)*.1 or dead/max(total,1)<.9
    red |= any(kinds.get(k,{}).get('killed',0)!=kinds.get(k,{}).get('total',0) for k in ['syntax_break','unknown_type'])
    # Every mutant must also fail at its expected stage.
    red |= bool(survivors)
    result={'canonical_total':len(sources),'canonical_pass':len(eligible),'canonical_fail':len(excluded),'excluded':excluded,'mutants':kinds,'mutant_total':total,'mutant_killed':dead,'kill_rate':dead/max(total,1),'unexpected_stages_or_survivors':survivors,'dataset_weaknesses':weaknesses,'wall_seconds':round(time.monotonic()-started,3),'red':bool(red)}
    result['new_validations']=audit.requests-initial_requests
    result['timing_note']='Wall time includes verdict-cache reuse; see throughput-summary.json for uncached validation throughput.'
    result['red_reasons']=[]
    if len(excluded)>len(sources)*.1:result['red_reasons'].append(f'{len(excluded)}/{len(sources)} canonical failures exceeds the 10% limit')
    if dead/max(total,1)<.9:result['red_reasons'].append('Overall mutation kill rate below 90%')
    if survivors:result['red_reasons'].append(f'{len(survivors)} mutants survive or fail at the wrong stage')
    dump(ROOT/'tools/osg-validator/gate-summary.json',result)
    return result


def sap_from_feedback(text):
    values={field:None for field in STAGES.values()}
    if text == 'The unit tests were successful.':
        return dict.fromkeys(STAGES.values(),True)
    if text.startswith('The syntax check failed'):
        values['sap_syntax_pass']=False
    elif text.startswith('The activation failed'):
        values.update(sap_syntax_pass=True,sap_activation_pass=False)
    elif text.startswith('The unittest syntax check failed'):
        values.update(sap_syntax_pass=True,sap_activation_pass=True,unit_syntax_pass=False)
    elif text.startswith('The unit test failed'):
        values.update(sap_syntax_pass=True,sap_activation_pass=True,unit_syntax_pass=True,unit_pass=False)
    return values


def cause(message):
    message=message.lower()
    if 'timeout' in message: return 'timeout'
    if any(s in message for s in ['sqlite','sql error','database','no such table','no such column']):return 'DB difference'
    if any(s in message for s in ['not found','unknown type','not exist','not a method','unknown method','not defined']):return 'missing library class/method/type or unresolved name'
    if any(s in message for s in ['cloud','parser_error','not supported','unexpected','statement does not exist']):return 'Cloud-rule or parser/transpiler difference'
    return 'runtime or semantic difference (manual review needed)'


def false_accept_cause(feedback):
    """Classify the earliest recorded SAP rejection, including downstream passes."""
    text=feedback.lower()
    fixtures = (p.stem.lower() for p in (ROOT / 'dataset/abap_tables').glob('*.abap'))
    if any(re.search(r'\b' + re.escape(name) + r'\b', text) for name in fixtures) and any(x in text for x in ('unknown','not declared','not active','does not exist')):
        return 'SAP fixture missing supplied dataset DDIC object'
    if any(x in text for x in ('not released','not permitted','not allowed','not supported in the current abap language','not available in this abap language')):
        return 'Cloud release or language restriction'
    if "'type': 'w'" in text and "'type': 'e'" not in text and feedback.startswith('The syntax check failed'):
        return 'SAP warnings treated as rejection by benchmark'
    if 'must be escaped' in text:
        return 'SAP strict SQL host-variable check'
    if 'field ' in text and 'is unknown' in text:
        return 'SAP name-resolution check'
    if any(x in text for x in ('must have the type','compatible','convert','type conflict','cannot be used','not a structure','not an internal table','generic type','fully typed')):
        return 'SAP type or conversion check'
    if any(x in text for x in ('uniquely','implementation','already declared','already defined','visibility','private','protected')):
        return 'SAP declaration or activation check'
    if feedback.startswith('The unittest syntax check failed'):
        return 'SAP unit-test syntax or contract mismatch'
    if feedback.startswith('The unit test failed'):
        return 'Runtime or assertion difference'
    if feedback.startswith('The syntax check failed'):
        return 'Other SAP syntax check (inspect feedback)'
    if feedback.startswith('The activation failed'):
        return 'Other SAP activation check (inspect feedback)'
    return 'Oracle not observed or feedback unclassified'


def calibrate(audit):
    data=json.loads((ROOT/'data/claude-opus-4-6.json').read_text())
    tiers=json.loads((ROOT/'data/claude-opus-4-6_tiers.json').read_text())
    items=[];oracle={};origins=collections.Counter();feedbacks={};recorded_ids=set()
    for task,chats in data.items():
        for rep,chat in enumerate(chats):
            round_number=0
            for index,msg in enumerate(chat):
                if msg['role']!='assistant':continue
                ident=f'{task}:{rep}:R{round_number}'
                items.append((ident,task,msg['content']))
                feedback=chat[index+1]['content'] if index+1<len(chat) and chat[index+1]['role']=='user' else ''
                inferred=sap_from_feedback(feedback)
                recorded=tiers.get(task,[])
                saved=recorded[rep].get(f'round_{round_number}') if rep<len(recorded) else None
                if saved:
                    inferred.update({f:saved.get(f) for f in STAGES.values() if f!='unit_syntax_pass'})
                    origins['tiers']+=1;recorded_ids.add(ident)
                else:origins['feedback_reconstruction']+=1
                oracle[ident]=inferred;feedbacks[ident]=feedback
                round_number+=1
    started=time.monotonic();answers=audit.run(items)
    stats={}
    for stage,field in STAGES.items():
        s={'answers':len(items),'agreement':0,'false_reject':0,'false_accept':0,'false_reject_causes':{},'false_reject_examples':[],'false_accept_examples':[], 'both_observed':0,'both_observed_agree':0}
        causes=collections.Counter();accept_causes=collections.Counter();accept_examples={}
        for ident,task,source in items:
            sap,osg=oracle[ident].get(field),answers[ident].get(field)
            if sap is not None and osg is not None:
                s['both_observed']+=1;s['both_observed_agree']+=sap==osg
            accepted_sap,accepted_osg=bool(sap),bool(osg)
            s['agreement']+=accepted_sap==accepted_osg
            kind='false_reject' if accepted_sap and not accepted_osg else 'false_accept' if accepted_osg and not accepted_sap else None
            if kind:
                s[kind]+=1
                category=cause(answers[ident].get('error_message') or '') if kind=='false_reject' else false_accept_cause(feedbacks[ident])
                if kind=='false_reject':causes[category]+=1
                else:
                    accept_causes[category]+=1
                    accept_examples.setdefault(category,{'answer':ident,'sap':sap,'osg':osg,'cause':category,'sap_feedback':feedbacks[ident][:900]})
                if len(s[kind+'_examples'])<20:
                    s[kind+'_examples'].append({'answer':ident,'sap':sap,'osg':osg,'cause':category,'osg_message':(answers[ident].get('error_message') or '')[:350],'sap_feedback':feedbacks[ident][:250],'source_excerpt':source[:200]})
        s['agreement_percent']=round(100*s['agreement']/len(items),3)
        s['false_reject_causes']=dict(causes)
        s['false_accept_causes']=dict(accept_causes)
        s['false_accept_examples_by_cause']=list(accept_examples.values())
        comparable=[ident for ident in recorded_ids if oracle[ident].get(field) is not None]
        s['recorded_tiers_only']={
            'comparable_answers':len(comparable),
            'agreement':sum(bool(oracle[i][field])==bool(answers[i].get(field)) for i in comparable),
            'false_reject':sum(bool(oracle[i][field]) and not bool(answers[i].get(field)) for i in comparable),
            'false_accept':sum(not bool(oracle[i][field]) and bool(answers[i].get(field)) for i in comparable),
        } if stage!='unit_syntax' else {'comparable_answers':0,'note':'Original tiers do not contain unit_syntax_pass; reconstructed from recorded feedback.'}
        stats[stage]=s
    def headline(verdicts):
        groups=collections.defaultdict(list)
        for ident,v in verdicts.items():groups[ident.rsplit(':R',1)[0]].append((int(ident.rsplit(':R',1)[1]),v))
        r0=sum(any(r==0 and v.get('unit_pass') for r,v in rows) for rows in groups.values())
        best=sum(any(v.get('unit_pass') for r,v in rows if r<=5) for rows in groups.values())
        return {'conversations':len(groups),'R0_unit_pass':r0,'R0_percent':round(100*r0/len(groups),3),'best_R0_R5_unit_pass':best,'best_R0_R5_percent':round(100*best/len(groups),3)}
    summary={'model':'claude-opus-4-6','answers':len(items),'unique_answers':len({(t,s) for _,t,s in items}),'oracle_sources':dict(origins),'stage_definition':'Cumulative pass: not reached/null counts as not accepted. Also report both-observed counts; missing tier rounds reconstructed from recorded feedback.','caveat':'Later rounds were generated after SAP feedback, not OSG feedback. This is a per-answer comparison, not a comparison of feedback trajectories.','stages':stats,'headline':{'sap':headline(oracle),'osg':headline(answers)},'wall_seconds':round(time.monotonic()-started,3)}
    # Preserve first measured wall time when regenerating only summary fields from cache.
    previous=ROOT/'tools/osg-validator/calibration-summary.json'
    if audit.requests==0 and previous.exists():
        summary['wall_seconds']=json.loads(previous.read_text())['wall_seconds']
        summary['timing_note']='Wall time retained from the original complete replay; summary regenerated from cached per-answer verdicts.'
    unique={audit.key(task,source):answers[ident] for ident,task,source in items}
    seconds=sum(v.get('elapsed_seconds') or 0 for v in unique.values())
    summary['throughput']={'unique_answers':len(unique),'summed_worker_seconds':round(seconds,3),'unique_answers_per_worker_second':round(len(unique)/max(seconds,.001),3)}
    dump(ROOT/'tools/osg-validator/calibration-summary.json',summary)
    # Full replay remains local and untracked.
    dump(audit.output/'replay.json',answers)
    return summary


def subset():
    excluded={x['task'] for x in json.loads((ROOT/'tools/osg-validator/excluded.json').read_text())}
    with (ROOT/'data/prompt_classification.csv').open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f,delimiter=';');rows=list(reader);labels=reader.fieldnames[3:]
    def allocation(priority):
        counts=collections.Counter()
        for row in rows:
            if row['HumanEval/Nr'].strip()+'.txt' in excluded:continue
            for label in priority:
                if row[label].strip():counts[label]+=1;break
        return sum(min(10,counts[label]) for label in priority)
    # Exhaustive six-label ordering; ties follow the CSV column order.
    priority=list(max(itertools.permutations(labels),key=allocation))
    buckets={label:[] for label in priority}
    for row in rows:
        task=row['HumanEval/Nr'].strip()+'.txt'
        if task in excluded:continue
        for label in priority:
            if row[label].strip():buckets[label].append(task);break
    rng=random.Random(20261024);selected={}
    for label in priority:
        candidates=sorted(buckets[label]);rng.shuffle(candidates);selected[label]=candidates[:10]
    result={'seed':20261024,'priority':priority,'priority_selection':'Maximize eligible slots capped at 10 per category; ties follow CSV column order.','requested_per_category':10,'categories':selected,'eligible_per_category':{l:len(v) for l,v in buckets.items()},'shortfalls':{l:10-len(v) for l,v in selected.items() if len(v)<10},'tasks':[t for tasks in selected.values() for t in tasks]}
    for label, shortfall in result['shortfalls'].items():
        print(f'{label}: taking all {len(selected[label])} eligible tasks; shortfall {shortfall} (informational)', flush=True)
    dump(ROOT/'tools/osg-validator/subset.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['gate','calibrate','subset','all'])
    parser.add_argument('--workers',type=int,default=3)
    parser.add_argument('--output',type=Path,default=ROOT/'osg-results')
    args=parser.parse_args();audit=Audit(args.workers,args.output);red=False
    try:
        if args.mode in ('gate','all'):
            result=gate(audit);red=result['red'];print(json.dumps({k:v for k,v in result.items() if k not in ('excluded','unexpected_stages_or_survivors')},indent=2),flush=True)
        if args.mode in ('subset','all'):print(json.dumps(subset(),indent=2),flush=True)
        if args.mode in ('calibrate','all'):
            result=calibrate(audit);print(json.dumps({'headline':result['headline'],'answers':result['answers']},indent=2),flush=True)
    finally:audit.close()
    return int(red)

if __name__=='__main__':sys.exit(main())
