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

from generate_llm_answers import SYSTEM_PROMPT
from generate_llm_answers_openai_direct import ask_openai_direct
from osg_validator import OsgClient, ROOT, evaluate
import openai


async def run(args):
    if not re.fullmatch(r'[\w.-]+', args.output_name):
        raise ValueError('Output name must contain only letters, digits, underscore, dot or hyphen')
    subset = json.loads(Path(args.subset).read_text())
    tasks = subset['tasks']
    if len(tasks) != len(set(tasks)):
        raise ValueError('Duplicate subset tasks')
    out = ROOT / 'data' / (args.output_name + '.json')
    tiers_out = out.with_name(args.output_name + '_tiers.json')
    if out.exists() or tiers_out.exists():
        raise ValueError('Output already exists; use abap_test/llm_generate to resume it')
    model = {'name': args.model, 'provider': 'OPENAI_DIRECT', 'max_tokens': args.max_tokens, 'temperature': 0.2}
    conversations, tiers = {}, {}
    validator = OsgClient()
    try:
        async with openai.AsyncOpenAI(base_url=args.base_url, api_key=args.api_key) as client:
            for task in tasks:
                if not re.fullmatch(r'(?:\d+|erp_\d+)\.txt', task):
                    raise ValueError('Invalid task identifier')
                conversations[task], tiers[task] = [], []
                for _ in range(args.repetitions):
                    chat = [{'role':'system','content':SYSTEM_PROMPT}, {'role':'user','content':(ROOT/'dataset/prompts'/task).read_text()}]
                    result = {}
                    conversations[task].append(chat); tiers[task].append(result)
                    for round_number in range(args.rounds):
                        code = await ask_openai_direct(client, model, chat)
                        chat.append({'role':'assistant','content':code})
                        tier, feedback = evaluate(validator, task, code)
                        chat.append({'role':'user','content':feedback})
                        result[f'round_{round_number}'] = tier
                        for dest,data in [(out,conversations),(tiers_out,tiers)]:
                            tmp=dest.with_suffix('.tmp');tmp.write_text(json.dumps(data,indent=2));tmp.replace(dest)
                        if tier['unit_pass']:
                            break
    finally:
        validator.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',required=True)
    parser.add_argument('--model',required=True)
    parser.add_argument('--api-key',default='local-test')
    parser.add_argument('--subset',default='tools/osg-validator/subset.json')
    parser.add_argument('--output-name',required=True)
    parser.add_argument('--repetitions',type=int,default=10)
    parser.add_argument('--rounds',type=int,choices=range(1,7),default=6)
    parser.add_argument('--max-tokens',type=int,default=8192)
    args=parser.parse_args()
    if args.repetitions<1:parser.error('--repetitions must be positive')
    asyncio.run(run(args))

if __name__=='__main__':main()
