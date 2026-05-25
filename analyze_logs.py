import json
import os
import re
from collections import Counter, defaultdict

log_dir = 'F:/下载/logs/'
files = []
for f in os.listdir(log_dir):
    if f.startswith('AutoAnime_operations_') and f.endswith('.json'):
        files.append(os.path.join(log_dir, f))

files.sort()
files = files[-500:]  # 最近500个

all_records = []
for f in files:
    try:
        with open(f, 'r', encoding='utf-8') as fp:
            data = json.load(fp)
            if isinstance(data, dict) and 'records' in data:
                all_records.extend(data['records'])
    except Exception as e:
        pass

output = []
output.append(f"分析 {len(files)} 个日志文件")
output.append(f"总记录数: {len(all_records)}")

if not all_records:
    output.append("无记录")
    with open('analyze_result.txt', 'w', encoding='utf-8') as f:
        f.write('\n'.join(output))
    exit()

# 查看字段
output.append(f"\n字段: {list(all_records[0].keys())}")

# 分类统计
status_counter = Counter()
action_counter = Counter()
message_counter = Counter()

for r in all_records:
    status_counter[r.get('status', 'unknown')] += 1
    action_counter[r.get('action', 'unknown')] += 1
    message_counter[r.get('message', 'unknown')] += 1

output.append("\n=== Status 分布 ===")
for s, c in status_counter.most_common():
    output.append(f"  {s}: {c}")

output.append("\n=== Action 分布 ===")
for a, c in action_counter.most_common():
    output.append(f"  {a}: {c}")

output.append("\n=== Message 分布 ===")
for m, c in message_counter.most_common(30):
    output.append(f"  {m}: {c}")

# 提取所有成功整理的记录，分析原标题和识别后的标题
output.append("\n=== 分析所有成功/失败记录中的标题识别问题 ===")

# 收集所有不同的番剧文件夹名
show_folders = Counter()
for r in all_records:
    dst = r.get('dst', '')
    if dst:
        parts = dst.replace('\\', '/').split('/')
        # 找到番剧文件夹 (Season的上级或dst中的某个文件夹)
        for i, p in enumerate(parts):
            if p.startswith('Season'):
                if i > 0:
                    show_folders[parts[i-1]] += 1
                break

output.append(f"\n共识别出 {len(show_folders)} 个不同的番剧名称")
output.append("\n最常出现的番剧名称 (前30):")
for name, c in show_folders.most_common(30):
    output.append(f"  {name}: {c}次")

# 分析特定问题
output.append("\n=== 详细案例分析 (前50条非skip记录) ===")
count = 0
for r in all_records:
    if r.get('action') != 'skip' and count < 50:
        src = r.get('src', '')
        dst = r.get('dst', '')
        msg = r.get('message', '')
        src_basename = os.path.basename(src)
        output.append(f"\n--- 案例 {count+1} ---")
        output.append(f"SRC: {src_basename}")
        output.append(f"DST: {dst}")
        output.append(f"MSG: {msg}")
        count += 1

# 分析 RSS 订阅的原始文件名规律
output.append("\n=== 分析原始文件名中的发布组/字幕组标记 ===")
group_tags = Counter()
for r in all_records:
    src = os.path.basename(r.get('src', ''))
    m = re.search(r'\[(.*?)\]', src)
    if m:
        group_tags[m.group(1)] += 1

output.append("\n发布组分布 (前20):")
for g, c in group_tags.most_common(20):
    output.append(f"  [{g}]: {c}次")

# 分析可能的问题: 日文原名 vs 中文译名
output.append("\n=== 可能的识别问题分析 ===")
# 找出dst中的标题和src中的标题差异较大的情况
problem_cases = []
for r in all_records:
    src = os.path.basename(r.get('src', ''))
    dst = r.get('dst', '')
    if not dst:
        continue
    parts = dst.replace('\\', '/').split('/')
    show_folder = None
    for i, p in enumerate(parts):
        if p.startswith('Season'):
            if i > 0:
                show_folder = parts[i-1]
            break
    if show_folder:
        # 简单启发式: 如果src中有英文标题但dst是纯中文，或反之
        has_eng = bool(re.search(r'[a-zA-Z]{4,}', src))
        has_chi = any('\u4e00' <= ch <= '\u9fff' for ch in show_folder)
        if has_eng and not has_chi:
            # 英文原文件名但识别出中文 - 这是正常的翻译
            pass
        # 记录一些特殊案例
        if len(show_folder) > 20:
            problem_cases.append((src, show_folder, 'long_name'))

output.append(f"\n发现 {len(problem_cases)} 个潜在问题案例")
for src, folder, reason in problem_cases[:20]:
    output.append(f"  [{reason}] {folder}")
    output.append(f"    SRC: {src}")

with open('analyze_result.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(output))

print("分析完成，结果已保存到 analyze_result.txt")
