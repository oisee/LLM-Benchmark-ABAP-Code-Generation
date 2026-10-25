#!/usr/bin/env python3
"""Run an explicit subset through the existing direct generator and OSG feedback.

Requires an explicit OpenAI-compatible URL and model. Nothing runs on import.
Outputs use the benchmark's existing conversation and tier formats.
"""
import argparse
import asyncio
import json
from pathlib import Path
import re
import time

from generate_llm_answers import SYSTEM_PROMPT
from generate_llm_answers_openai_direct import ask_openai_direct
from osg_validator import InfrastructureError, OsgClient, ROOT, evaluate
import openai


async def run(args):
    if not re.fullmatch(r'[\w.-]+', args.output_name):
        raise ValueError('Output name must contain only letters, digits, underscore, dot or hyphen')
    subset = json.loads(Path(args.subset).read_text())
    tasks = subset['tasks']
    if len(tasks) != len(set(tasks)):
        raise ValueError('Duplicate subset tasks')
    for task in tasks:
        if not re.fullmatch(r'(?:\d+|erp_\d+)\.txt', task):
            raise ValueError('Invalid task identifier')
    temperature = getattr(args, 'temperature', 0.2)
    plan = {'tasks': tasks, 'task_count': len(tasks), 'rounds': [f'R{r}' for r in range(args.rounds)],
            'repetitions': args.repetitions, 'model': args.model, 'temperature': temperature,
            'max_tokens': args.max_tokens, 'request_timeout_s': getattr(args, 'request_timeout', 600.0),
            'extra_body': getattr(args, 'extra_body', None) or {},
            'concurrency': 1, 'validator': 'osg',
            'maximum_requests': len(tasks) * args.repetitions * args.rounds}
    if getattr(args, 'dry_run', False):
        print(json.dumps(plan, indent=2))
        return plan
    out = ROOT / 'data' / (args.output_name + '.json')
    tiers_out = out.with_name(args.output_name + '_tiers.json')
    manifest_out = out.with_name(args.output_name + '_manifest.json')
    result_out = out.with_name(args.output_name + '_result.json')
    if any(p.exists() for p in (out, tiers_out, manifest_out, result_out)):
        raise ValueError('Output already exists; use abap_test/llm_generate to resume it')
    model = {'name': args.model, 'provider': 'OPENAI_DIRECT', 'max_tokens': args.max_tokens, 'temperature': temperature,
             'extra_body': plan['extra_body']}
    manifest_out.write_text(json.dumps(plan, indent=2) + '\n')
    conversations, tiers, results = {}, {}, {}
    validator = OsgClient()
    try:
        async with openai.AsyncOpenAI(base_url=args.base_url, api_key=args.api_key, max_retries=0,
                                      timeout=plan['request_timeout_s']) as client:
            for task in tasks:
                if not re.fullmatch(r'(?:\d+|erp_\d+)\.txt', task):
                    raise ValueError('Invalid task identifier')
                conversations[task], tiers[task], results[task] = [], [], []
                for _ in range(args.repetitions):
                    chat = [{'role':'system','content':SYSTEM_PROMPT}, {'role':'user','content':(ROOT/'dataset/prompts'/task).read_text()}]
                    result, round_results = {}, {}
                    results[task].append(round_results)
                    conversations[task].append(chat); tiers[task].append(result)
                    for round_number in range(args.rounds):
                        metadata = {}
                        started = time.monotonic()
                        code = await ask_openai_direct(client, model, chat, request_metadata=metadata, force_temperature=True)
                        metadata['elapsed_s'] = round(time.monotonic() - started, 3)
                        round_results[f'round_{round_number}'] = metadata
                        chat.append({'role':'assistant','content':code})
                        try:
                            tier, feedback = evaluate(validator, task, code)
                        except InfrastructureError as exc:
                            # Save the answer and observed stages, with no model feedback.
                            # Its assistant tail remains NeedsSAPTest for the resume CLI.
                            result[f'round_{round_number}'] = exc.tier
                            raise
                        chat.append({'role':'user','content':feedback})
                        result[f'round_{round_number}'] = tier
                        for dest,data in [(out,conversations),(tiers_out,tiers),(result_out,results)]:
                            tmp=dest.with_suffix('.tmp');tmp.write_text(json.dumps(data,indent=2));tmp.replace(dest)
                        if tier['unit_pass']:
                            break
    finally:
        try:
            for dest, data in [(out, conversations), (tiers_out, tiers), (result_out, results)]:
                tmp = dest.with_suffix('.tmp')
                tmp.write_text(json.dumps(data, indent=2))
                tmp.replace(dest)
        finally:
            validator.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',required=True)
    parser.add_argument('--model',required=True)
    parser.add_argument('--api-key',default='local-test')
    parser.add_argument('--subset',default='tools/osg-validator/subset.json')
    parser.add_argument('--output-name',required=True)
    parser.add_argument('--repetitions',type=int,default=1)
    parser.add_argument('--rounds',type=int,choices=range(1,7),default=6)
    parser.add_argument('--max-tokens',type=int,default=5000)
    parser.add_argument('--temperature',type=float,default=0.2)
    parser.add_argument('--request-timeout',type=float,default=600.0,help='Seconds per model request (openai client default 600); raise it for long local generations')
    parser.add_argument('--extra-body',type=json.loads,default=None,help='JSON object of extra request fields, e.g. {"top_k":20,"chat_template_kwargs":{"enable_thinking":false}}')
    parser.add_argument('--dry-run',action='store_true',help='Print tasks, rounds and request parameters without starting a client')
    args=parser.parse_args()
    if args.max_tokens<1:parser.error('--max-tokens must be positive')
    if args.extra_body is not None and not isinstance(args.extra_body,dict):parser.error('--extra-body must be a JSON object')
    if args.request_timeout<=0:parser.error('--request-timeout must be positive')
    if args.repetitions<1:parser.error('--repetitions must be positive')
    asyncio.run(run(args))

if __name__=='__main__':main()
