# -*- coding: utf-8 -*-
"""
一次性缓存修复脚本：将「没有辣妹会对阿宅温柔」相关记录修正为「哪里有温柔对待阿宅的辣妹！？」。

修复内容：
1. 备份 .cache/titles.json 与 .cache/organization.json。
2. 在 titles.json 中迁移 canonical 记录（key、zh 均改为目标标题）。
3. 更新所有指向旧 CID 的 alias 记录。
4. 在 organization.json 中迁移记录（key、canonical_id、title_zh），并同步 episode_last_dst 路径中的目录名。

注意：本脚本只修改缓存 JSON，不会移动磁盘上的实际视频文件。
"""

import json
import shutil
from datetime import datetime
from pathlib import Path


CACHE_DIR = Path(__file__).resolve().parent.parent / '.cache'
TITLES_PATH = CACHE_DIR / 'titles.json'
ORG_PATH = CACHE_DIR / 'organization.json'

OLD_ZH = '没有辣妹会对阿宅温柔！？'
NEW_ZH = '哪里有温柔对待阿宅的辣妹！？'
OLD_CID = '没有辣妹会对阿宅温柔'
NEW_CID = '哪里有温柔对待阿宅的辣妹！？'
MATCH_EN = 'Otaku ni Yasashii Gal wa Inai'


def _backup(path: Path) -> Path:
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(f'{path.suffix}.bak.{ts}')
    shutil.copy2(path, bak)
    return bak


def _load_json(path: Path) -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _save_json(path: Path, data: dict) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')


def fix_titles(data: dict) -> dict:
    canonicals = data.get('canonicals', {})
    aliases = data.get('aliases', {})

    # 定位目标 canonical：优先用英文/罗马音匹配，同时命中旧 key 或旧 zh 也接受
    target_old_keys = []
    for cid, rec in list(canonicals.items()):
        if not isinstance(rec, dict):
            continue
        en = rec.get('en', '') or ''
        romaji = rec.get('romaji', '') or ''
        zh = rec.get('zh', '') or ''
        if (en == MATCH_EN or romaji == MATCH_EN or cid == OLD_CID or zh == OLD_ZH):
            target_old_keys.append(cid)

    if not target_old_keys:
        raise RuntimeError('未找到匹配的 canonical 记录')

    for old_cid in target_old_keys:
        if old_cid == NEW_CID:
            # 已经修复，跳过
            continue
        rec = canonicals.pop(old_cid)
        rec['zh'] = NEW_ZH
        canonicals[NEW_CID] = rec
        print(f'[titles] canonical 迁移: {old_cid} -> {NEW_CID}')

    updated_aliases = 0
    for akey, arec in list(aliases.items()):
        if isinstance(arec, dict):
            cid = arec.get('canonical_id', '')
        elif isinstance(arec, str):
            cid = arec
        else:
            continue
        if cid in target_old_keys or cid == OLD_CID:
            if isinstance(arec, dict):
                arec['canonical_id'] = NEW_CID
            else:
                aliases[akey] = NEW_CID
            updated_aliases += 1
    print(f'[titles] 更新 aliases: {updated_aliases} 条')

    data['__meta__']['updated_at'] = datetime.now().isoformat()
    return data


def fix_organization(data: dict) -> dict:
    records = data.get('records', {})
    # 通过旧 zh、旧 CID 或英文/罗马音匹配目标记录
    target_old_keys = []
    for k, rec in list(records.items()):
        if not isinstance(rec, dict):
            continue
        title_en = rec.get('title_en', '') or ''
        title_romaji = rec.get('title_romaji', '') or ''
        title_zh = rec.get('title_zh', '') or ''
        if (title_en == MATCH_EN or title_romaji == MATCH_EN or
                k == OLD_CID or title_zh == OLD_ZH):
            target_old_keys.append(k)

    if not target_old_keys:
        print('[organization] 未找到匹配记录（可能已修复）')
        return data

    for old_cid in target_old_keys:
        if old_cid == NEW_CID:
            # 已经修复，跳过
            continue
        rec = records.pop(old_cid)
        rec['canonical_id'] = NEW_CID
        rec['title_zh'] = NEW_ZH

        # 同步 episode_last_dst 中的目录名
        ep_dst = rec.get('episode_last_dst', {})
        for tag, dst in list(ep_dst.items()):
            if isinstance(dst, str):
                ep_dst[tag] = dst.replace(OLD_ZH, NEW_ZH)

        records[NEW_CID] = rec
        print(f'[organization] 记录迁移: {old_cid} -> {NEW_CID}')

    data['__meta__']['updated_at'] = datetime.now().isoformat()
    return data


def main():
    if not TITLES_PATH.is_file() or not ORG_PATH.is_file():
        raise FileNotFoundError(f'缓存文件不存在: {TITLES_PATH} / {ORG_PATH}')

    titles_bak = _backup(TITLES_PATH)
    org_bak = _backup(ORG_PATH)
    print(f'已备份: {titles_bak.name}, {org_bak.name}')

    titles_data = _load_json(TITLES_PATH)
    org_data = _load_json(ORG_PATH)

    titles_data = fix_titles(titles_data)
    org_data = fix_organization(org_data)

    _save_json(TITLES_PATH, titles_data)
    _save_json(ORG_PATH, org_data)
    print('缓存修复完成')


if __name__ == '__main__':
    main()
