import json
from collections import defaultdict

with open('.cache/titles.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

aliases = data.get('aliases', {})
canonicals = data.get('canonicals', {})

# 反向映射：canonical_id -> 所有alias
reverse_map = defaultdict(list)
for alias, info in aliases.items():
    cid = info.get('canonical_id', '')
    reverse_map[cid].append((alias, info.get('source', ''), info.get('trust_level', 0)))

# 找出可疑的映射：同一个canonical_id关联了明显不同的番剧
suspicious = []
for cid, alias_list in reverse_map.items():
    if len(alias_list) >= 5:
        # 检查这些alias是否来自完全不同的番剧
        # 通过长度和关键词来判断
        # 收集英文alias（通常来自原文件名）
        en_aliases = [a for a, s, t in alias_list if all(ord(c) < 128 for c in a)]
        if len(en_aliases) >= 3:
            suspicious.append((cid, alias_list))

print(f"=== 发现 {len(suspicious)} 个可疑的canonical映射 ===\n")

for cid, alias_list in suspicious:
    print(f"Canonical: {cid}")
    print(f"  共有 {len(alias_list)} 个别名:")
    for alias, source, trust in sorted(alias_list, key=lambda x: x[2], reverse=True):
        print(f"    [{source}:{trust}] {alias}")
    print()

# 特别检查一些已知的错误映射
known_bad_patterns = [
    ('mairimashita', '欢迎来到实力至上主义的教室'),
    ('iruma', '欢迎来到实力至上主义的教室'),
    ('wistoria', '钢之炼金术师'),
    ('replica', '约会大作战'),
    ('shunkashuutou', '猫娘咖啡馆'),
    ('bang dream', '哆啦A梦'),
]

print("=== 检查已知的错误模式 ===\n")
for keyword, wrong_canonical in known_bad_patterns:
    for cid, alias_list in reverse_map.items():
        if wrong_canonical in cid:
            matches = [a for a, s, t in alias_list if keyword.lower() in a.lower()]
            if matches:
                print(f"找到污染! keyword='{keyword}' 被映射到 '{cid}'")
                print(f"  匹配别名: {matches}")
                print()
