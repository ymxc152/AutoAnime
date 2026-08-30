"""
autoanime 通用网络请求工具

对应原 `AutoAnimeMv.py`:
- `Auxiliary_Http`
- `Auxiliary_PROXY`
"""

from os import environ
from urllib.request import getproxies

from requests import exceptions, get, post

from .. import state
from ..config_loader import Auxiliary_GetTMDBBearerToken, Auxiliary_ParseInt
from ..logging_utils import Auxiliary_Log


def Auxiliary_PROXY():
    '''代理

    行为优先级：
    1. USESYSPROXY=True 时优先读取并使用系统代理；
    2. USEPROXY=True 时使用 config.ini 中手动配置的代理；
    3. 两者都为 False 时显式关闭代理，避免 requests 自动读取系统代理导致意外。'''
    if state.USESYSPROXY == True:
        Auxiliary_Log('使用系统代理')
        ProxyTuple = tuple(getproxies().values())
        if ProxyTuple != ():
            state.HTTPPROXY, state.HTTPSPROXY, _ = ProxyTuple
        else:
            state.HTTPPROXY, state.HTTPSPROXY = '', ''
        environ['http_proxy'] = state.HTTPPROXY
        environ['https_proxy'] = state.HTTPSPROXY
        environ['all_proxy'] = state.ALLPROXY
    elif state.USEPROXY == True:
        Auxiliary_Log('代理功能开启')
        environ['http_proxy'] = state.HTTPPROXY
        environ['https_proxy'] = state.HTTPSPROXY
        environ['all_proxy'] = state.ALLPROXY
    else:
        # 不主动设置代理时，保留环境变量，让 requests 自行读取用户配置的系统/环境代理
        pass


def Auxiliary_Http(Url, flag='GET', JsonData=None, ExtraHeaders=None, Timeout=30, ResponseType='text'):
    '''网络请求，支持 JSON 解析与字段校验前置'''

    headers = {'User-Agent': f'AutoAnimeMv/{state.Versions}'}
    if type(ExtraHeaders) == dict:
        headers.update(ExtraHeaders)
    if 'themoviedb' in Url:
        TMDBToken = Auxiliary_GetTMDBBearerToken()
        if TMDBToken not in [None, '']:
            headers['Authorization'] = f'Bearer {TMDBToken}'
        else:
            Auxiliary_Log('TMDB token 未配置，TMDBApi 将不可用。请设置环境变量 TMDB_BEARER_TOKEN', 'WARNING')

    RetryTimes = Auxiliary_ParseInt(state.NETERRRECTRYTIMS, 1)
    if RetryTimes < 0:
        RetryTimes = 0
    for i in range(RetryTimes + 1):
        try:
            if str(flag).upper() != 'GET':
                HttpData = post(Url, json=JsonData, headers=headers, timeout=Timeout)
            else:
                HttpData = get(Url, headers=headers, timeout=Timeout)
            if HttpData.status_code == 200:
                if ResponseType == 'json':
                    try:
                        return HttpData.json()
                    except ValueError:
                        Auxiliary_Log(f'接口返回不是合法 JSON: {Url}', 'WARNING')
                        return None
                return HttpData.text.replace(r'\/', r'/')
            Auxiliary_Log(f'HttpData Status Code = {HttpData.status_code}', 'WARNING')
        except exceptions.ConnectionError:
            Auxiliary_Log(f'访问 {Url} 失败,请检查代理与网络连通性', 'WARNING')
        except exceptions.RequestException as err:
            Auxiliary_Log(f'访问 {Url} 失败: {err}', 'WARNING')
        except Exception as err:
            Auxiliary_Log(f'访问 {Url} 失败,未能获取到内容: {err}', 'WARNING')
        Auxiliary_Log(f'第{i+1}/{RetryTimes+1}次尝试失败', 'WARNING')
    return None
