type Details = Record<string, unknown> | null | undefined

function asRecord(details: unknown): Details {
  return details && typeof details === 'object' && !Array.isArray(details) ? details as Record<string, unknown> : null
}

function text(details: Details, key: string): string {
  const value = details?.[key]
  return value === undefined || value === null ? '' : String(value)
}

const MESSAGES: Record<string, string | ((details: Details) => string)> = {
  'Storage root is used by a scan profile; delete the profile first': details => {
    const name = text(details, 'profile')
    return name
      ? `该目录正被扫描方案「${name}」使用，请先删除对应扫描方案`
      : '该目录正被扫描方案使用，请先删除对应扫描方案'
  },
  'Storage root contains recorded files and cannot be deleted': details => {
    const files = text(details, 'files')
    return files
      ? `该目录下仍有 ${files} 条文件记录，无法删除`
      : '该目录下仍有已记录的文件，无法删除'
  },
  'Unsupported root kind': '不支持的目录类型',
  'Source and library roots cannot use the same path': '下载源与媒体库不能使用同一路径',
  'Storage root already exists': '该目录已经添加过了',
  'Library root cannot equal or be below a source root': '媒体库不能与下载源相同，也不能位于下载源内部',
  'Existing library root cannot be below the new source root': '已有媒体库位于该下载源内部，无法添加',
  'Storage root does not exist': '目录不存在',
  'Only the enabled state can be changed for an existing root; add a new root to change paths': '已添加的目录只能启用或停用；如需更换路径，请新增目录',
  'Root update is empty': '目录更新内容为空',
  'Root enabled state must be true or false': '目录启用状态必须为是或否',
  'Operation target must be relative to its root': '操作目标必须相对于所属目录',
  'Operation target escapes its registered root': '操作目标超出了已登记的目录范围',

  'Unsupported operation mode': '不支持的文件模式',
  'Unsupported execution policy': '不支持的执行策略',
  'Minimum confidence must be between 0 and 100': '最低置信度必须在 0 到 100 之间',
  'Stability seconds cannot be negative': '稳定等待秒数不能为负数',
  'Profile source must reference a source root': '扫描方案的下载源必须指向下载源目录',
  'Profile library must reference a library root': '扫描方案的媒体库必须指向媒体库目录',
  'Scan profile does not exist': '扫描方案不存在',
  'Scan profile was changed by another request': '扫描方案已被其他操作修改，请刷新后重试',
  'Scan profile has scan history and cannot be deleted; disable it instead': '该扫描方案已有扫描记录，无法删除，请改为停用',
  'Scan profile has plan history and cannot be deleted; disable it instead': '该扫描方案已有整理计划，无法删除，请改为停用',
  'Unsupported profile fields': '包含不支持的扫描方案字段',
  'Profile update is empty': '扫描方案更新内容为空',
  'Profile name cannot be empty': '扫描方案名称不能为空',
  'Profile numeric fields must contain integers': '扫描方案的数值字段必须是整数',
  'Profile boolean fields must be true or false': '扫描方案的开关字段必须为是或否',
  'Source and library roots cannot be equal or nested': '下载源与媒体库不能相同或相互嵌套',

  'Administrator username or password is too short': '管理员用户名过短或密码不足 12 位',
  'Administrator has already been created': '管理员账号已经创建过了',
  'Passwordless local login is only available on loopback': '本机免密登录仅限 127.0.0.1 访问',
  'Local passwordless login is disabled': '本机免密登录已关闭',
  'No active administrator is available': '没有可用的管理员账号',
  'Invalid username or password': '用户名或密码不正确',
  'Session is expired or invalid': '登录已过期或无效，请重新登录',
  'Secret value cannot be empty': '密钥不能为空',
  'Authentication is required': '需要先登录',
  'CSRF token is required': '缺少安全校验令牌，请刷新页面后重试',
  'CSRF token does not match the session': '安全校验令牌与当前会话不匹配，请刷新页面后重试',
  'Too many failed login attempts': '登录失败次数过多，请稍后再试',
  'The first administrator must be created from the local machine': '首次管理员必须在服务器本机创建',

  'Folder picker is only available on the local machine': '文件夹选择仅本机可用（请用 127.0.0.1 打开控制台，或直接粘贴路径）',
  'Folder dialog failed': '无法打开系统文件夹选择器',
  'Native folder picker is only available on Windows': '系统文件夹选择器仅支持 Windows',
  'Windows DPAPI is only available on Windows': 'Windows 密钥保护仅在 Windows 上可用',

  'Local hook endpoint is only available on loopback': '本机 Hook 仅限 127.0.0.1 访问',
  'Local trusted hooks are disabled': '本机受信任 Hook 已关闭',
  'No enabled scan profile is available for local hook': '没有可用于本机 Hook 的已启用扫描方案',

  'Setting key cannot be empty': '设置项不能为空',
  'Setting value must be a boolean': '该设置必须为开关值',
  'Setting value must be an integer': '该设置必须为整数',
  'OpenAI timeout must be at least 5 seconds': 'AI 超时必须至少 5 秒',
  'Metadata timeout must be at least 2 seconds': '元数据超时必须至少 2 秒',
  'Setting value must be a non-empty string': '该设置不能为空',
  'OpenAI base URL must start with http:// or https://': 'API Base URL 必须以 http:// 或 https:// 开头',
  'parse.agent_mode must be one of off / uncertain / all': 'AI 参与方式必须是关闭、仅低置信度或全部文件',
  'Setting does not exist at the requested revision': '设置已被其他操作修改，请刷新后重试',
  'Unsupported secret key': '不支持的密钥类型',

  'Agent session does not exist': '纠错会话不存在',
  'Show does not exist': '番剧不存在',
  'Agent session kind must be review or library': '纠错会话类型无效',
  'Message content is required': '请先填写要发送的内容',
  'Agent session is not open': '纠错会话已结束',
  'Agent session has no proposal to apply': '当前没有可应用的提案',
  'A title is required for a library correction': '纠正资料库标题时必须填写标题',

  'Unknown schedule timezone': '未知的计划时区',
  'interval_minutes must be an integer greater than zero': '扫描间隔必须是大于 0 的整数分钟',
  'Daily schedule time must use HH:MM': '每天时间必须使用 HH:MM 格式',
  'Unsupported schedule kind': '不支持的计划类型',
  'Schedule does not exist': '定时计划不存在',
  'Unsupported or empty schedule update': '计划更新无效或为空',
  'Schedule revision is stale': '定时计划已被其他操作修改，请刷新后重试',
  'Schedule enabled state must be true or false': '计划启用状态必须为是或否',
  'Webhook name and downloader are required': 'Webhook 名称和下载器不能为空',
  'Webhook source does not exist': 'Webhook 不存在',
  'Unsupported or empty webhook update': 'Webhook 更新无效或为空',
  'Webhook source revision is stale': 'Webhook 已被其他操作修改，请刷新后重试',
  'Webhook update contains invalid values': 'Webhook 更新包含无效值',
  'Webhook requires path or paths': 'Webhook 需要提供路径',
  'Enabled webhook source does not exist': '没有已启用的 Webhook',
  'Webhook path is outside the configured source root': 'Webhook 路径不在该方案的下载源目录内',

  'Restore requires maintenance mode': '恢复备份需要先进入维护模式',
  'Backup record does not exist': '备份记录不存在',
  'Backup file is missing or its checksum changed': '备份文件缺失或校验已变化',
  'Backup schema or integrity validation failed': '备份结构或完整性校验失败',

  'Rule set does not exist': '规则集不存在',
  'Rule revision does not exist': '规则修订不存在',
  'Rule document is invalid': '规则 JSON 无效',
  'Rule revision must be validated before activation': '规则修订必须先校验再激活',
  'Revision cannot be activated for this rule set': '该修订不能激活到当前规则集',

  'Plan does not exist': '整理计划不存在',
  'Active plans cannot be ignored': '进行中的计划不能忽略',
  'Plan has operation history and cannot be ignored': '该计划已有整理记录，无法忽略',
  'Plan still has open review items and cannot be ignored': '该计划仍有待确认项，无法忽略',
  'Unsupported plan item decision': '不支持的计划项决定',
  'A reject reason is required': '拒绝时必须填写原因',
  'Plan item decisions cannot change in the current state': '当前状态下不能再修改计划项决定',
  'Plan item does not exist': '计划项不存在',
  'Only an open plan can be executed': '只能执行尚未完成的计划',
  'Plan still contains conflicts': '计划中仍有路径冲突',
  'Plan execution was already claimed': '该计划已在执行中',
  'Plan inputs changed after plan approval': '批准后扫描方案或规则已变化，计划已过期',
  'Plan has no executable items': '没有可执行的计划项',
  'One operation batch must use a single file mode': '同一批次只能使用一种文件模式',
  'Dry-run plans cannot be approved or executed': '仅预览计划不能批准或执行',
  'Scan profile changed after plan creation': '扫描方案在计划生成后已变化',
  'Active rules changed after plan creation': '规则在计划生成后已变化',
  'Active rules changed after plan approval': '规则在计划批准后已变化',
  'Plan cannot be approved in its current state': '当前状态下不能批准该计划',
  'Open reviews or conflicts prevent plan approval': '仍有待确认项或路径冲突，无法批准计划',
  'Plan does not meet the automatic safety threshold': '计划未达到自动整理的安全阈值',
  'Plan cannot be executed in its current state': '当前状态下不能执行该计划',
  'Plan has no approved items to organize': '没有已批准且可整理的项目',
  'Source file disappeared before execution': '执行前源文件已不存在',
  'Source file changed before execution': '执行前源文件已变化',
  'Source file is missing': '源文件缺失',
  'Source file identity changed': '源文件内容已变化',
  'Destination is occupied': '目标位置已被占用',
  'Destination became occupied after preview': '预览后目标位置已被占用',
  'Destination is occupied by an untracked file': '目标位置已被未登记的文件占用',
  'Destination escapes its registered library root': '目标路径超出了已登记的媒体库范围',
  'Destination resolves outside its registered library root': '目标路径解析后超出了已登记的媒体库范围',
  'Destination path contains a symlink or reparse point': '目标路径包含符号链接或重解析点，无法使用',
  'Plan entry source has not been observed': '计划项对应的源文件尚未被扫描到',
  'Scan scope is outside the configured source root': '扫描范围不在该方案的下载源目录内',
  'Setting was changed by another request': '设置已被其他操作修改，请刷新后重试',
  'Hardlink source and destination are on different volumes': '硬链接的源与目标不在同一磁盘卷',

  'Operation batch does not exist': '整理批次不存在',
  'Only a completed correction can be rolled back': '只能回滚已完成的纠正',
  'Only a completed execution batch can be rolled back': '只能回滚已完成的整理批次',
  'Operation batch rollback was already claimed': '该批次已在回滚中',
  'Operation batch was already rolled back': '该批次已经回滚过了',
  'Operation log is missing': '操作日志缺失，无法回滚',
  'Correction log is missing': '纠正日志缺失，无法回滚',

  'Change request does not exist': '修改请求不存在',
  'Show changed after the editor loaded it': '番剧在打开后已被修改，请刷新后重试',
  'Show changed before applying the request': '番剧在应用修改前已变化，请刷新后重试',
  'Change request is not in a validated state': '修改请求尚未通过校验',
  'A canonical title is required': '必须填写规范标题',

  'Season must be a non-negative integer': '季度必须是非负整数',
  'Episode must be a non-negative number or safe episode label': '集号必须是非负数或安全标签',
  'Episode must be non-negative': '集号不能为负数',
  'Episode must be a finite non-negative number': '集号必须是有效的非负数',
  'Episode must be a number or string label': '集号必须是数字或标签',
  'Episode label contains unsupported characters': '集号包含不支持的字符',
  'Resolution must be an object': '确认信息格式无效',
  'Unsupported resolution field': '包含不支持的确认字段',
  'Title is required': '标题不能为空',
  'Title is too long': '标题过长',
  'is_movie must be a boolean': '是否电影必须为是或否',
  'Media type must be a string': '媒体类型必须是文本',
  'Media type must be episode, movie, or special': '媒体类型必须是单集、电影或特别篇',
  'is_movie conflicts with media_type': '是否电影与媒体类型不一致',
  'Release tag must be a string': '发布标签必须是文本',
  'Release tag is too long': '发布标签过长',
  'Manual lock must be a boolean': '人工锁必须为是或否',
  'Movie resolutions must not include season or episode': '电影不能填写季度或集号',
  'Season is required for episodes': '单集必须填写季度',
  'Episode is required': '必须填写集号',
  'Review item does not exist': '待确认项不存在',
  'Review item is not open': '该待确认项已处理',
  'Review item is not being resolved': '该待确认项当前不在处理中',
  'Resolved review produced an incomplete plan entry': '确认后生成的计划项不完整',
  'Resolved review destination conflicts with an existing plan item': '确认后的目标路径与已有计划项冲突',

  'Media file does not exist': '媒体文件不存在',
  'Job does not exist': '任务不存在',
  'Job cannot be cancelled in its current state': '当前状态下不能取消该任务',
  'Cancellation is not pending at a safe boundary': '当前不在可安全取消的节点',
  'Worker does not own the leased job': '该任务正由其他进程处理',
  'Worker cannot renew this job lease': '无法续订任务租约',
  'Worker cannot complete this job': '无法完成该任务',
  'Worker cannot fail this job': '无法将任务标记为失败',
  'Worker lease expired before completion': '任务租约在完成前过期',
}

const CODES: Record<string, string> = {
  validation_error: '请求无效',
  not_found: '未找到相关记录',
  duplicate_root: '该目录已经添加过了',
  unsafe_root: '目录路径不安全',
  path_outside_root: '路径超出已登记目录范围',
  revision_conflict: '数据已被其他操作修改，请刷新后重试',
  authentication_failed: '认证失败',
  login_throttled: '登录失败次数过多，请稍后再试',
  csrf_validation_failed: '安全校验失败，请刷新页面后重试',
  already_bootstrapped: '管理员账号已经创建过了',
  bootstrap_local_only: '首次管理员必须在服务器本机创建',
  local_only: '该操作仅本机可用',
  folder_dialog_failed: '无法打开系统文件夹选择器',
  lease_conflict: '任务正由其他进程处理',
  invalid_state: '当前状态不允许此操作',
  execution_policy_forbidden: '当前执行策略不允许此操作',
  stale_plan: '计划已过期，请重新扫描',
  plan_conflict: '计划存在冲突',
  immutable_plan: '该计划不可再修改',
  http_error: '请求失败',
}

const HTTP_STATUS: Record<number, string> = {
  400: '请求无效',
  401: '需要先登录或会话已过期',
  403: '没有权限执行此操作',
  404: '未找到相关记录',
  409: '操作冲突，请刷新后重试',
  422: '请求内容无效',
  429: '请求过于频繁，请稍后再试',
  500: '服务器内部错误',
  502: '服务暂时不可用',
  503: '服务暂时不可用',
}

const HTTP_STATUS_TEXT: Record<string, string> = {
  'Bad Request': '请求无效',
  Unauthorized: '需要先登录或会话已过期',
  Forbidden: '没有权限执行此操作',
  'Not Found': '未找到相关记录',
  Conflict: '操作冲突，请刷新后重试',
  'Unprocessable Entity': '请求内容无效',
  'Too Many Requests': '请求过于频繁，请稍后再试',
  'Internal Server Error': '服务器内部错误',
  'Bad Gateway': '服务暂时不可用',
  'Service Unavailable': '服务暂时不可用',
}

function looksEnglish(message: string): boolean {
  return /[A-Za-z]/.test(message) && !/[㐀-鿿]/.test(message)
}

function resolveMapped(message: string, details: Details): string | null {
  const mapped = MESSAGES[message]
  if (typeof mapped === 'function') return mapped(details)
  if (typeof mapped === 'string') return mapped
  return null
}

export function translateApiMessage(code: string, message: string, details: unknown = null, status = 0): string {
  const trimmed = String(message || '').trim()
  const record = asRecord(details)
  const mapped = trimmed ? resolveMapped(trimmed, record) : null
  if (mapped) return mapped
  if (trimmed && !looksEnglish(trimmed)) return trimmed
  if (trimmed && HTTP_STATUS_TEXT[trimmed]) return HTTP_STATUS_TEXT[trimmed]
  if (code && code !== 'http_error' && CODES[code]) return CODES[code]
  if (status && HTTP_STATUS[status]) return HTTP_STATUS[status]
  if (CODES[code]) return CODES[code]
  return trimmed || '操作失败'
}

export function extractApiFailure(body: unknown, status: number, statusText: string): { code: string; message: string; details: unknown } {
  const record = asRecord(body) || {}
  const code = typeof record.code === 'string' && record.code ? record.code : 'http_error'
  let message = typeof record.message === 'string' ? record.message : ''
  if (!message && typeof record.error === 'string') message = record.error
  const detail = record.detail
  if (!message && typeof detail === 'string') message = detail
  if (!message && Array.isArray(detail)) {
    message = detail
      .map(item => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object' && 'msg' in item) return String((item as { msg?: unknown }).msg || '')
        return ''
      })
      .filter(Boolean)
      .join('；')
  }
  const details = record.details !== undefined ? record.details : (detail ?? null)
  return {
    code,
    message: translateApiMessage(code, message || statusText, details, status),
    details,
  }
}
