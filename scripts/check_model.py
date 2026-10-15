"""Explicit, bounded live check using synthetic data. Never prints credentials."""
import argparse
import asyncio
import json
from pathlib import Path

from backend.app.model_gateway import ModelGateway, ModelGatewayError


async def check():
    gateway = ModelGateway()
    if not gateway.configured:
        return {'status':'not_configured'}
    try:
        result=await gateway.generate('只根据演示记录，简短说明示例用户的学习目标。',[
            {'path':'synthetic-only/demo.md','content':'虚构演示人物小林的学习目标是用 Python 写一个本地笔记工具。此记录不是用户个人信息。'}])
        return {'status':'ok','mode':result['mode'],'model':result['model'],
                'usage':result['usage'],'answer':result['answer']}
    except ModelGatewayError as error:
        return {'status':'failed','code':error.code,'message':error.message}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',help='perform one billed request using only synthetic data')
    args=parser.parse_args()
    if not args.live:
        parser.error('Pass --live to authorize one synthetic-data request.')
    report=asyncio.run(check())
    from backend.app.config import DATA_DIR
    folder=DATA_DIR/'diagnostics'
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'model-smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
