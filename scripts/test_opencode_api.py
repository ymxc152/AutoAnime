# -*- coding: utf-8 -*-
"""临时脚本：验证 opencode 免费模型配置能否通过项目真实代码路径完成识别。

用法: python scripts/test_opencode_api.py [文件名]
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoanime import state
from autoanime.cli import Start_PATH


def main():
    Start_PATH()
    print('=== 配置生效检查 ===')
    print(f'OPENAI_BASE_URL = {state.OPENAI_BASE_URL}')
    print(f'OPENAI_MODEL    = {state.OPENAI_MODEL}')
    print(f'OPENAI_MODELS   = {state.OPENAI_MODELS}')
    print(f'OPENAI_API_KEY  = {(state.OPENAI_API_KEY or "")[:12]}...(len={len(state.OPENAI_API_KEY or "")})')
    assert 'opencode.ai' in state.OPENAI_BASE_URL, 'BASE_URL 未生效'
    assert state.OPENAI_MODEL, 'MODEL 未生效'
    assert state.OPENAI_API_KEY and state.OPENAI_API_KEY.startswith('sk-'), 'API KEY 未生效'

    from autoanime.apis.openai_client import Auxiliary_GetOpenAIEndpointSlots
    slots = Auxiliary_GetOpenAIEndpointSlots()
    print(f'=== 槽位({len(slots)}) ===')
    for i, (u, k, m) in enumerate(slots, 1):
        print(f'  slot{i}: url={u} model={m} key={k[:12]}...')

    test_file = sys.argv[1] if len(sys.argv) > 1 else '[Subbers] 鬼灭之刃 无限列车篇 第01话 [1080p].mkv'

    from autoanime.identification.openai_identify import Auxiliary_OpenAIIdentifyFileInfo

    print('=== 调用 OpenAI 识别 ===')
    result = Auxiliary_OpenAIIdentifyFileInfo(test_file)
    if result is None:
        print('识别返回 None')
        print('LastOpenAIIdentifyFailure =', state.LastOpenAIIdentifyFailure)
        sys.exit(1)
    se, ep, raw_se, raw_ep, raw_name = result
    print(f'识别成功 => SE={se} EP={ep} RAWSE={raw_se} RAWEP={raw_ep} RAWName={raw_name}')
    assert raw_name not in [None, ''], 'RAWName 为空'
    print('=== opencode API 端到端测试通过 ===')


if __name__ == '__main__':
    main()
