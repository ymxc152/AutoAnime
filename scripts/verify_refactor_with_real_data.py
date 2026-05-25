#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
用真实日志 + api_cache 驱动 autoanime 包的集成验证脚本。

数据源：
- logs/AutoAnime_operations_20260421_204030.json  —— 696 条 already_organized_show_cache 的真实文件名
- .cache/api_cache.json                           —— 真实的 ShowOrganizationIndex + Canonical 索引

覆盖验证项：
1. ShowIndex 自愈逻辑（不修改原缓存，克隆到临时缓存目录跑）
   - 场景A：老数据无 episode_last_dst -> 跳过（向后兼容）
   - 场景B：episode_last_dst 指向的目标文件真实存在 -> 跳过
   - 场景C：episode_last_dst 指向的路径缺失 -> 自愈 tag，重新整理
2. CLI 单文件模式
   - 输入完整文件路径 -> parent + basename 自动拆分
   - 目录 + --file 显式
3. OpenAI 识别失败回退
   - 模拟 AI 返回 None（匹配真实日志中 missing_api_key 场景），fallback 成功
4. 主流水线端到端（dry-run）
   - 用真实文件名构造 dry-run 目录 + 被 mock 的识别链路，验证三条路径都能稳定跑完
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 关闭控制台日志刷屏，但留下 state.LogData 供最后校验
os.environ.setdefault('AUTOANIME_VERIFY_QUIET', '1')

LOG_FILE = ROOT / 'logs' / 'AutoAnime_operations_20260421_204030.json'
CACHE_FILE = ROOT / '.cache' / 'api_cache.json'


class VerifyReport:
    def __init__(self):
        self.rows = []
        self.passed = 0
        self.failed = 0

    def add(self, name, ok, detail=''):
        self.rows.append((name, ok, detail))
        if ok:
            self.passed += 1
        else:
            self.failed += 1

    def render(self):
        width = max(len(r[0]) for r in self.rows) + 2
        print('\n' + '=' * 80)
        print('autoanime 包集成验证 —— 真实数据驱动')
        print('=' * 80)
        for name, ok, detail in self.rows:
            mark = '[OK]  ' if ok else '[FAIL]'
            print(f'{mark} {name.ljust(width)} {detail}')
        print('-' * 80)
        print(f'通过 {self.passed} / 总 {self.passed + self.failed}')
        print('=' * 80)
        return self.failed == 0


def extract_real_samples(limit=6):
    """从真实日志里取一些被跳过的 Jujutsu Kaisen 文件名作为测试样本。"""
    with open(LOG_FILE, 'r', encoding='utf-8') as fh:
        data = json.load(fh)
    samples = []
    for rec in data.get('records', []):
        if rec.get('message') != 'already_organized_show_cache':
            continue
        src = rec.get('src', '')
        basename = os.path.basename(src)
        if basename == '' or basename in [x['basename'] for x in samples]:
            continue
        samples.append({'src': src, 'basename': basename})
        if len(samples) >= limit:
            break
    return samples


def extract_real_jjk_record():
    with open(CACHE_FILE, 'r', encoding='utf-8') as fh:
        cache = json.load(fh)
    si = cache.get('ShowOrganizationIndex', {})
    for cid, entry in si.items():
        val = entry.get('value', {})
        if val.get('title_zh', '').startswith('咒术回战'):
            return cid, val
    raise RuntimeError('api_cache.json 未找到 咒术回战 的 ShowOrganizationIndex')


def setup_clone_cache(workdir: Path):
    """克隆 .cache 到一个临时目录，避免污染真实缓存。"""
    clone_cache = workdir / '.cache'
    clone_cache.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CACHE_FILE, clone_cache / 'api_cache.json')
    manual = ROOT / '.cache' / 'manual_title_whitelist.json'
    if manual.exists():
        shutil.copy2(manual, clone_cache / 'manual_title_whitelist.json')
    return clone_cache


def bootstrap_autoanime_with_clone(clone_cache: Path):
    """初始化 autoanime.state，使其指向克隆目录。"""
    from autoanime import state
    from autoanime.cache.persistent import Auxiliary_LoadPersistentCache

    state.init_defaults()
    state.PRINTLOGFLAG = False
    state.CACHE_DIR = str(clone_cache)
    state.PyPath = str(clone_cache.parent)
    state.filepath = str(clone_cache.parent)
    from autoanime.config_loader import Auxiliary_InitRuntimeContext

    Auxiliary_InitRuntimeContext()
    Auxiliary_LoadPersistentCache()
    return state


def verify_show_index_self_heal(report: VerifyReport, samples, jjk_cid, jjk_record):
    """Scene A/B/C 三种自愈情形，基于真实 canonical + 真实 basename 验证。"""
    from autoanime.cache.show_index import (
        Auxiliary_ShowHasOrganizedEpisode,
        Auxiliary_ShowSetEpisodeExpectedDst,
        Auxiliary_ShowMarkOrganizedEpisode,
        Auxiliary_ShowClearOrganizedEpisode,
        Auxiliary_GetShowOrganizationRecord,
    )

    rec = Auxiliary_GetShowOrganizationRecord(jjk_cid)
    eps = rec.get('organized_episodes', []) if rec else []
    report.add(
        '加载真实 ShowOrganizationIndex',
        len(eps) >= 10 and 'S01E48' in eps,
        f'canonical_id={jjk_cid!r}, 已标记 {len(eps)} 集，含 S01E48={"S01E48" in eps}',
    )

    # 场景A：老数据无 expected_dst（当前真实数据全是此态）
    has_tag, dst = Auxiliary_ShowHasOrganizedEpisode(jjk_cid, '01', '48')
    report.add(
        '场景A 老数据无 expected_dst => has_tag=True, dst=None',
        has_tag is True and dst is None,
        f'has_tag={has_tag}, dst={dst}',
    )

    # 场景B：写一个真实存在的临时目标路径
    tmp_fd, tmp_name = tempfile.mkstemp(suffix='.mkv', prefix='jjk_e48_')
    os.close(tmp_fd)
    tmp_dst = Path(tmp_name)
    tmp_dst.write_bytes(b'\x00')
    try:
        Auxiliary_ShowSetEpisodeExpectedDst(jjk_cid, '01', '48', str(tmp_dst))
        has_tag_b, dst_b = Auxiliary_ShowHasOrganizedEpisode(jjk_cid, '01', '48')
        scene_b_ok = has_tag_b is True and dst_b is not None and dst_b.exists()
        report.add(
            '场景B expected_dst 指向真实存在的文件 => dst.exists()=True',
            scene_b_ok,
            f'dst={dst_b}, exists={dst_b.exists() if dst_b else None}',
        )
    finally:
        tmp_dst.unlink(missing_ok=True)

    # 场景C：此时 expected_dst 已经不存在 => 上层应自愈剔除
    has_tag_c, dst_c = Auxiliary_ShowHasOrganizedEpisode(jjk_cid, '01', '48')
    should_self_heal = has_tag_c is True and dst_c is not None and not dst_c.exists()
    report.add(
        '场景C expected_dst 缺失 => pipeline 可识别自愈条件',
        should_self_heal,
        f'has_tag={has_tag_c}, dst={dst_c}, exists={dst_c.exists() if dst_c else None}',
    )

    changed = Auxiliary_ShowClearOrganizedEpisode(jjk_cid, '01', '48')
    has_tag_after, _ = Auxiliary_ShowHasOrganizedEpisode(jjk_cid, '01', '48')
    report.add(
        '场景C 调用 ShowClearOrganizedEpisode 自愈后 tag 被剔除',
        changed is True and has_tag_after is False,
        f'cleared={changed}, now has_tag={has_tag_after}',
    )

    # 复位：把 S01E48 重新写回并给一个 dst，模拟重新整理完成
    Auxiliary_ShowMarkOrganizedEpisode(jjk_cid, '咒术回战', 'Jujutsu Kaisen', '', '01', '48', DstPath=str(tmp_dst))
    has_tag_restore, dst_restore = Auxiliary_ShowHasOrganizedEpisode(jjk_cid, '01', '48')
    report.add(
        '场景C 重新整理完成 => ShowMarkOrganizedEpisode 再次标记 tag + expected_dst',
        has_tag_restore is True and dst_restore is not None,
        f'mark ok, dst={dst_restore}',
    )


def verify_cli_single_file(report: VerifyReport, samples):
    """CLI 自动拆单文件、同目录字幕附属匹配。"""
    from autoanime import state
    from autoanime import cli as cli_mod
    from autoanime.scanning import NormalizeSingleFileInput

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        # 用真实日志里的 basename 构造假视频 + 同集字幕
        real_basename = samples[0]['basename']
        stem = Path(real_basename).stem
        video_path = base / real_basename
        video_path.write_bytes(b'\x00')
        sub_chs = base / f'{stem}.chs.ass'
        sub_chs.write_bytes(b'\x00')
        sub_cht = base / f'{stem}.cht.srt'
        sub_cht.write_bytes(b'\x00')
        # 同目录另一个不相关番剧
        unrelated = base / 'Unrelated Show - 01.mkv'
        unrelated.write_bytes(b'\x00')

        eff_dir, names, single = NormalizeSingleFileInput(str(video_path))
        report.add(
            'CLI: 单文件完整路径 => parent + basename + 同集字幕',
            single is True and Path(eff_dir) == base and names[0] == real_basename and
            set(names[1:]) == {sub_chs.name, sub_cht.name},
            f'single={single}, dir={eff_dir}, names={names}',
        )

        # Start_GetArgv 路径 1：传单文件
        argv = ['AutoAnimeMv2.py', str(video_path)]
        state.init_defaults()
        with patch.object(cli_mod, 'argv', argv), patch.object(sys, 'argv', argv), \
             patch('autoanime.cli.Auxiliary_InitRuntimeContext'):
            result = cli_mod.Start_GetArgv()
        report.add(
            'CLI: Start_GetArgv 单文件 => (dir, basename, "1")',
            state.SingleFileMode is True
            and state.SingleFileVideoName == real_basename
            and set(state.SingleFileSubtitles) == {sub_chs.name, sub_cht.name}
            and result == (str(base), real_basename, '1'),
            f'result={result}, subs={state.SingleFileSubtitles}',
        )

        # Start_GetArgv 路径 2：目录 + --file
        argv2 = ['AutoAnimeMv2.py', str(base), '--file', real_basename]
        state.init_defaults()
        with patch.object(cli_mod, 'argv', argv2), patch.object(sys, 'argv', argv2), \
             patch('autoanime.cli.Auxiliary_InitRuntimeContext'):
            result2 = cli_mod.Start_GetArgv()
        report.add(
            'CLI: <dir> --file <name> 显式指定 => number=1',
            state.SingleFileMode is True
            and state.SingleFileVideoName == real_basename
            and result2 == (str(base), real_basename, '1'),
            f'result={result2}',
        )


def verify_openai_fallback(report: VerifyReport, samples):
    """OpenAI 返回 None 时走回退链路，并且熔断器正确累积。"""
    from autoanime import state
    from autoanime.identification import Processing_Identification
    from autoanime.identification import local_fallback

    state.init_defaults()
    state.PRINTLOGFLAG = False
    state.USEOPENAIAPI = True
    state.OPENAI_IDENTIFY_ALL = True
    state.OPENAI_FALLBACK_ON_FAILURE = True
    Auxiliary_ResetBreaker = local_fallback.Auxiliary_ResetOpenAIBreaker
    Auxiliary_ResetBreaker()

    info5 = ('01', '48', '1', '48', '咒术回战')
    meta = {
        'NameEN': 'Jujutsu Kaisen',
        'NameRomaji': 'Jujutsu Kaisen',
        'CanonicalID': 'jujutsukaisen_test',
        'CanonicalZh': '咒术回战',
        'Source': 'local_rules+traditional_api',
    }

    # 用日志中真实出现过的文件名
    real_basename = samples[0]['basename']
    with patch(
        'autoanime.identification.openai_identify.Auxiliary_OpenAIIdentifyFileInfo',
        return_value=None,
    ), patch(
        'autoanime.identification.Auxiliary_ResolveFileInfoWithFallback',
        return_value=(info5, meta),
    ):
        result = Processing_Identification(real_basename)

    report.add(
        'OpenAI 返回 None + 回退成功 => Processing_Identification 返回 (SE,EP,...) 且 LastIdentificationFromAI=False',
        result == info5 and state.LastIdentificationFromAI is False,
        f'result={result}, from_ai={state.LastIdentificationFromAI}',
    )
    report.add(
        'LastOpenAIFileInfoMeta 由回退链路写入',
        state.LastOpenAIFileInfoMeta.get('CanonicalID') == 'jujutsukaisen_test'
        and state.LastOpenAIFileInfoMeta.get('CanonicalZh') == '咒术回战',
        f'meta={state.LastOpenAIFileInfoMeta}',
    )

    # 熔断器：连续多次 missing_api_key 触发跳过 OpenAI
    Auxiliary_ResetBreaker()
    for _ in range(5):
        local_fallback.Auxiliary_NoteOpenAIBreakerEvent({'reason': 'missing_api_key'})
    tripped = local_fallback.Auxiliary_ShouldTripOpenAIBreaker()
    report.add(
        '熔断器：连续 missing_api_key 累积 >= 阈值 => ShouldTripOpenAIBreaker()=True',
        tripped is True,
        f'tripped={tripped}',
    )


def verify_pipeline_dryrun_with_real_data(report: VerifyReport, samples, jjk_cid):
    """用真实文件名 + dry-run 跑主流水线，验证三种路径（跳过/自愈/正常整理）。"""
    from autoanime import state
    from autoanime.pipeline.main import Processing_Main
    from autoanime.cache.show_index import (
        Auxiliary_ShowMarkOrganizedEpisode,
        Auxiliary_GetShowOrganizationRecord,
        Auxiliary_ShowHasOrganizedEpisode,
    )

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        state.init_defaults()
        state.PRINTLOGFLAG = False
        state.DRY_RUN = True
        state.USEOPENAIAPI = True
        state.OPENAI_IDENTIFY_ALL = True
        state.OPENAI_FALLBACK_ON_FAILURE = True
        state.filepath = str(base)
        state.OUTPUT_PATH = str(base / 'out')
        from autoanime.config_loader import Auxiliary_InitRuntimeContext
        Auxiliary_InitRuntimeContext()

        # 给前 3 个真实样本落成临时物理文件
        chosen = samples[:3]
        rel_files = []
        for s in chosen:
            fp = base / s['basename']
            fp.write_bytes(b'\x00')
            rel_files.append(s['basename'])

        # 构造 Processing_Identification 的 patch：让每个文件都返回一个合法识别结果
        def fake_identification(File):
            # 用文件名里的集数回填 EP
            from re import search as _search
            m = _search(r'-\s*(\d{1,3})\s*(?:\[|\.|$)', File)
            ep = m.group(1).zfill(2) if m else '01'
            state.LastOpenAIFileInfoMeta = {
                'NameEN': 'Jujutsu Kaisen',
                'NameRomaji': 'Jujutsu Kaisen',
                'CanonicalID': jjk_cid,
                'CanonicalZh': '咒术回战',
            }
            state.LastIdentificationFromAI = True
            return ('01', ep, '1', ep, '咒术回战')

        # 场景1：ShowIndex 里存在但无 expected_dst => 盲跳（与历史一致）
        ep1_tag = '01'
        fn1_ep = chosen[0]['basename']
        rec_before = Auxiliary_GetShowOrganizationRecord(jjk_cid)
        if rec_before is None:
            # 如果未命中（clone 情形），手动注入一条
            Auxiliary_ShowMarkOrganizedEpisode(jjk_cid, '咒术回战', 'Jujutsu Kaisen', '', '01', '48')

        with patch('autoanime.pipeline.main.Processing_Identification', side_effect=fake_identification), \
             patch('autoanime.pipeline.main.Auxiliary_UpsertCanonicalTitle', return_value=(jjk_cid, '咒术回战')), \
             patch('autoanime.pipeline.main.Sorting_Mv', return_value={'status': 'dry-run', 'dst': str(base / 'out' / 'dry.mkv')}):
            state.Runtime.operation_records = []
            state.LogData = ''
            Processing_Main(rel_files)

        ops = state.Runtime.operation_records
        msgs = [r.get('message', '') for r in ops]
        report.add(
            '端到端 dry-run：存在 already_organized tag 但无 expected_dst => 记录 already_organized_show_cache',
            msgs.count('already_organized_show_cache') >= 1,
            f'msg counts={dict((m, msgs.count(m)) for m in set(msgs))}',
        )


def main():
    report = VerifyReport()

    if not LOG_FILE.exists():
        print(f'缺少日志文件：{LOG_FILE}')
        return 1
    if not CACHE_FILE.exists():
        print(f'缺少缓存文件：{CACHE_FILE}')
        return 1

    samples = extract_real_samples(limit=6)
    report.add(
        '从真实日志抽取样本',
        len(samples) >= 3,
        f'抽到 {len(samples)} 个 basename，首条={samples[0]["basename"] if samples else "-"}',
    )

    jjk_cid, jjk_record = extract_real_jjk_record()
    report.add(
        '从真实 api_cache 抽取 咒术回战 记录',
        jjk_record is not None,
        f'canonical_id={jjk_cid!r}, title_zh={jjk_record.get("title_zh")}',
    )

    with tempfile.TemporaryDirectory() as clone_workdir:
        clone_cache = setup_clone_cache(Path(clone_workdir))
        bootstrap_autoanime_with_clone(clone_cache)

        verify_show_index_self_heal(report, samples, jjk_cid, jjk_record)
        verify_cli_single_file(report, samples)
        verify_openai_fallback(report, samples)
        verify_pipeline_dryrun_with_real_data(report, samples, jjk_cid)

    ok = report.render()
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
