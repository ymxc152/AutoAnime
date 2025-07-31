# AutoAnime - 动漫文件自动整理工具

[![Python](https://img.shields.io/badge/Python-3.6+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-GPL--3.0-green.svg)](LICENSE)

基于 [AutoAnimeMV-Teams/AutoAnimeMv](https://github.com/AutoAnimeMV-Teams/AutoAnimeMv) 的二次开发版本，用于自动整理和重命名动漫文件。

# 💡 功能说明
* **部署快速,使用方便的番剧视频/字幕重命名+整理工具**
  >   
      动漫
      ├── 因为太怕痛就全点防御力了
      │   ├── Season01
      │   │   ├── E01.mp4
      │   │   ├── E02.mp4
      │   │   ├── E03.mp4
      │   │   └── ...
      │   └── Season02
      │       ├── E01.mp4
      │       ├── E02.mp4
      │       ├── E02.chi.srt
      │       └── ...
      |___ 碧蓝之海
      |    └── Season01  
      │        ├── E01.mp4
      │        ├── E01.chs.ass
      │        ├── E02.mp4
      │        └── ....
      |
      ......
  
* **一次配置,无感使用**
* **支持硬链接配置,保种必备**
* **支持番剧分类,让一切井井有条**
* **本地批处理和QB下载模式任君选择**
* **快速更新,享受更多新体验**

***
## ✨ 主要功能

- 🎯 智能识别动漫文件名中的季数和集数信息
- 🖼️ 自动从多个API获取动漫封面图片
- 📝 支持ASS字幕文件的自动整理
- 🔗 支持创建硬链接以节省存储空间
- 🌐 支持Bangumi、TMDB、BGM等多个数据源
- ⚙️ 丰富的配置选项

## 🚀 快速开始

### 安装依赖
```bash
pip install requests zhconv
```

### 使用方法
```bash
# 处理指定文件
python AutoAnimeMv.py "文件路径"

# 查看帮助
python AutoAnimeMv.py help
```

## 🎬 支持格式

- **视频**: MP4, MKV, AVI, MOV, WMV, FLV
- **字幕**: ASS, SSA
- **命名**: `[字幕组] 动漫名 S01E01 [1080p].mp4`, `动漫名 第01话.mp4`, `动漫名 01.mp4`

## 📄 许可证

本项目采用 GPL-3.0 许可证，基于 [AutoAnimeMV-Teams/AutoAnimeMv](https://github.com/AutoAnimeMV-Teams/AutoAnimeMv) 进行二次开发。

## 🙏 致谢

- [AutoAnimeMV-Teams/AutoAnimeMv](https://github.com/AutoAnimeMV-Teams/AutoAnimeMv) - 原项目
- [Bangumi API](https://bangumi.github.io/api/) - 动漫数据API
- [TMDB API](https://www.themoviedb.org/documentation/api) - 影视数据库API

## 📞 联系

- 提交 [Issue]
- 邮箱：ymxc152@qq.com

---

⭐ 如果这个项目对你有帮助，请给它一个星标！ 