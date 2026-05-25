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
files = files[-500:]

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

# 1. 分析同一原文件名的不同识别结果
output.append("=== 1. 分析同一原文件名/相似文件名是否被识别为不同结果 ===")

# 2. 重点找出明显的错误识别案例
# 规则：如果src中的中文名/日文名/英文名和dst中的中文名完全对不上
output.append("\n=== 2. 严重误识别案例分析 ===")

mismatches = []
for r in all_records:
    src = os.path.basename(r.get('src', ''))
    dst = r.get('dst', '')
    if not dst:
        continue
    
    # 提取dst中的番剧名
    parts = dst.replace('\\', '/').split('/')
    show_folder = None
    for i, p in enumerate(parts):
        if p.startswith('Season'):
            if i > 0:
                show_folder = parts[i-1]
            break
    
    if not show_folder:
        continue
    
    # 从src中提取可能的标题
    # 去掉发布组标签
    src_clean = re.sub(r'^[\[【].*?[\]】]\s*', '', src)
    # 去掉集数和后缀
    src_clean = re.sub(r'\s*-?\s*\d+\s*\[.*$', '', src_clean)
    src_clean = re.sub(r'\.(mkv|mp4|avi)$', '', src_clean)
    
    # 简单判断：如果src中有明确的中文标题，但和dst完全不同
    # 或者src中有明确的英文/日文标题，但dst是毫不相关的名字
    
    # 收集一些已知严重错误的模式
    known_wrong = {
        '入间同学入魔了': ['欢迎来到实力至上主义的教室'],
        'Mairimashita': ['欢迎来到实力至上主义的教室'],
        'Replica datte': ['约会大作战'],
        '複製品': ['他和她的故事'],
        '左撇子': ['进击的巨人'],
        'Wistoria': ['钢之炼金术师'],
        'Shunkashuutou': ['猫娘咖啡馆'],
        'GANSO BanG Dream': ['哆啦A梦'],
        '天使': ['关于我转生变成史莱姆这档事'],  # 邻家天使 -> 史莱姆
        'Niwatori Fighter': ['公鸡斗士'],  # 这个是对的
        'Kusuriya no Hitorigoto': ['药屋少女的呢喃'],  # 这个是对的
    }
    
    is_wrong = False
    for keyword, wrong_names in known_wrong.items():
        if keyword.lower() in src_clean.lower():
            if any(w in show_folder for w in wrong_names):
                is_wrong = True
                break
    
    # 额外判断：一些明显的跨番剧错误
    if not is_wrong:
        # 如果src中有明确的中文名，但dst中完全没有相关字
        # 提取src中的中文字符
        src_chinese = ''.join(re.findall(r'[\u4e00-\u9fff]', src_clean))
        dst_chinese = ''.join(re.findall(r'[\u4e00-\u9fff]', show_folder))
        
        # 如果src有中文标题（>=4个字），且dst也有中文标题，且两者完全不重叠
        if len(src_chinese) >= 4 and len(dst_chinese) >= 2:
            # 计算重叠度
            overlap = set(src_chinese) & set(dst_chinese)
            if len(overlap) == 0:
                # 但有例外：有些翻译差异很大是正常的
                # 排除一些合理的翻译差异
                pass
    
    if is_wrong:
        mismatches.append((src, show_folder, dst))

output.append(f"发现 {len(mismatches)} 个确认的误识别案例:")
for src, folder, dst in mismatches:
    output.append(f"\nSRC: {src}")
    output.append(f"  → 识别为: {folder}")

# 3. 分析同一dst番剧名的来源文件名多样性
# 如果同一个番剧名来自完全不同的文件名，可能是缓存污染
output.append("\n=== 3. 同一番剧名的来源多样性分析 (可能暗示缓存污染) ===")
show_to_sources = defaultdict(list)
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
        show_to_sources[show_folder].append(src)

# 找出来源文件名差异很大的番剧
for show, sources in show_to_sources.items():
    if len(sources) >= 3:
        # 检查这些来源是否都包含相似的关键词
        # 简单检查：去掉发布组后的标题是否相似
        cleaned = []
        for s in sources:
            c = re.sub(r'^[\[【].*?[\]】]\s*', '', s)
            c = re.sub(r'\s*-?\s*\d+.*$', '', c)
            cleaned.append(c.lower())
        
        # 如果 cleaned 之间差异很大
        unique_cleaned = list(set(cleaned))
        if len(unique_cleaned) >= 3:
            # 检查是否有明显的不同番剧被归为同一类
            output.append(f"\n番剧: {show}")
            output.append(f"  来源多样性高 ({len(unique_cleaned)} 种不同文件名):")
            for u in unique_cleaned[:5]:
                output.append(f"    - {u}")

# 4. 分析 Season/Episode 提取错误
output.append("\n=== 4. Season/Episode 异常案例分析 ===")
for r in all_records:
    src = os.path.basename(r.get('src', ''))
    dst = r.get('dst', '')
    
    # 从src提取集数
    src_ep_match = re.search(r'\s-\s*(\d+)|\[(\d+)\]|\s(\d+)\s*\[', src)
    src_ep = None
    if src_ep_match:
        src_ep = int(next(g for g in src_ep_match.groups() if g is not None))
    
    # 从dst提取集数
    dst_ep_match = re.search(r'S\d+E(\d+)', dst)
    dst_ep = None
    if dst_ep_match:
        dst_ep = int(dst_ep_match.group(1))
    
    if src_ep and dst_ep and src_ep != dst_ep:
        output.append(f"\n集数不匹配!")
        output.append(f"  SRC: {src} (集数: {src_ep})")
        output.append(f"  DST: {dst} (集数: {dst_ep})")

# 5. Season提取异常
output.append("\n=== 5. Season 异常案例分析 ===")
for r in all_records:
    src = os.path.basename(r.get('src', ''))
    dst = r.get('dst', '')
    
    # 从src提取季数
    src_season = None
    if '第一季' in src or '第1季' in src or 'S1' in src or 'Season 1' in src:
        src_season = 1
    elif '第二季' in src or '第2季' in src or 'S2' in src or 'Season 2' in src:
        src_season = 2
    elif '第三季' in src or '第3季' in src or 'S3' in src or 'Season 3' in src:
        src_season = 3
    elif '第四季' in src or '第4季' in src or 'S4' in src or 'Season 4' in src:
        src_season = 4
    
    dst_season_match = re.search(r'S(\d+)E', dst)
    dst_season = int(dst_season_match.group(1)) if dst_season_match else None
    
    if src_season and dst_season and src_season != dst_season:
        output.append(f"\n季数不匹配!")
        output.append(f"  SRC: {src} (推断季: {src_season})")
        output.append(f"  DST: {dst} (识别季: {dst_season})")

with open('analyze_result2.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(output))

print("分析完成")
