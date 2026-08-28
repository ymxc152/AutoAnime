import { describe, expect, it } from 'vitest'

import { extractApiFailure, translateApiMessage } from './errors'

describe('translateApiMessage', () => {
  it('translates the storage-root-in-use error and includes the profile name', () => {
    expect(translateApiMessage(
      'validation_error',
      'Storage root is used by a scan profile and cannot be deleted; disable the root instead',
      { root_id: 3, profile: '默认配置' },
    )).toBe('该目录正被扫描方案「默认配置」使用，无法删除，请直接停用该目录')
  })

  it('falls back without a profile name', () => {
    expect(translateApiMessage(
      'validation_error',
      'Storage root is used by a scan profile and cannot be deleted; disable the root instead',
    )).toBe('该目录正被扫描方案使用，无法删除，请直接停用该目录')
  })

  it('keeps already-Chinese messages', () => {
    expect(translateApiMessage('http_error', '规则 JSON 格式无效')).toBe('规则 JSON 格式无效')
  })

  it('uses a code fallback for unknown English text', () => {
    expect(translateApiMessage('not_found', 'Some unknown English error')).toBe('未找到相关记录')
  })

  it('translates login and CSRF messages', () => {
    expect(translateApiMessage('authentication_failed', 'Invalid username or password')).toBe('用户名或密码不正确')
    expect(translateApiMessage('csrf_validation_failed', 'CSRF token is required')).toBe('缺少安全校验令牌，请刷新页面后重试')
  })

  it('translates extra executor and settings messages', () => {
    expect(translateApiMessage('validation_error', 'Destination became occupied after preview')).toBe('预览后目标位置已被占用')
    expect(translateApiMessage('revision_conflict', 'Setting was changed by another request')).toBe('设置已被其他操作修改，请刷新后重试')
  })

  it('translates logical-delete profile guard messages', () => {
    expect(translateApiMessage(
      'validation_error',
      'Scan profile has an active scan job and cannot be deleted; wait or cancel it first',
    )).toBe('该扫描方案仍有正在运行的扫描任务，请等待任务完成或先取消任务')
    expect(translateApiMessage(
      'validation_error',
      'Scan profile has been deleted and cannot be changed',
    )).toBe('该扫描方案已删除，无法修改')
    expect(translateApiMessage(
      'validation_error',
      'Scan profile has been deleted and cannot start new scans',
    )).toBe('该扫描方案已删除，无法发起新的扫描')
  })
})

describe('extractApiFailure', () => {
  it('reads DomainError JSON and returns Chinese', () => {
    const failure = extractApiFailure(
      { code: 'validation_error', message: 'Storage root already exists', details: { path: 'F:\\src' } },
      422,
      'Unprocessable Entity',
    )
    expect(failure.message).toBe('该目录已经添加过了')
    expect(failure.code).toBe('validation_error')
  })

  it('translates HTTP statusText when the body has no message', () => {
    expect(extractApiFailure({}, 401, 'Unauthorized').message).toBe('需要先登录或会话已过期')
  })
})
