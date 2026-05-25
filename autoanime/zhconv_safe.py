"""
zhconv 词典的安全预加载

对应原 `AutoAnimeMv.py::Auxiliary_InitZhconvDictionarySafely`。
原版使用 `zhconv.get_module_res` 打开资源流并人工关闭；
本模块按计划改用 `importlib.resources.files(...).open('rb')` 的 with 上下文，
确保即便出错也会释放句柄，消除 `ResourceWarning`。
"""

import json

import zhconv.zhconv as zhconv_module


def Auxiliary_InitZhconvDictionarySafely():
    '''安全预加载 zhconv 词典，避免第三方资源句柄泄漏警告'''
    try:
        if getattr(zhconv_module, 'zhcdicts', None) is not None:
            return
        DictFile = getattr(zhconv_module, 'DICTIONARY', 'zhcdict.json')
        DefaultDictFile = getattr(zhconv_module, '_DEFAULT_DICT', 'zhcdict.json')
        RawBytes = b''

        if DictFile == DefaultDictFile:
            Loaded = False
            try:
                # 优先 importlib.resources，with 上下文负责关闭句柄
                from importlib.resources import files as resource_files
                Resource = resource_files('zhconv').joinpath(DictFile)
                with Resource.open('rb') as f:
                    RawBytes = f.read()
                Loaded = True
            except Exception:
                Loaded = False
            if Loaded == False and hasattr(zhconv_module, 'get_module_res'):
                ResourceStream = zhconv_module.get_module_res(DictFile)
                if ResourceStream not in [None, '']:
                    try:
                        RawBytes = ResourceStream.read()
                    finally:
                        if hasattr(ResourceStream, 'close'):
                            try:
                                ResourceStream.close()
                            except Exception:
                                pass
        else:
            with open(DictFile, 'rb') as f:
                RawBytes = f.read()

        if RawBytes in [None, b'']:
            return
        DictData = json.loads(RawBytes.decode('utf-8'))
        DictData['SIMPONLY'] = frozenset(DictData.get('SIMPONLY', []))
        DictData['TRADONLY'] = frozenset(DictData.get('TRADONLY', []))
        zhconv_module.zhcdicts = DictData
    except Exception:
        return
