/*
 * FolderPickerDialog 单测(P1-D):盘符根加载/下钻/面包屑回跳/空目录/错误重试/
 * 选择回调与禁用态。mock 走 spyOn api.filesystem(不经 mocks/handlers.ts)。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, vi } from 'vitest'
import { FolderPickerDialog } from '../FolderPickerDialog'
import { api } from '../../api'

afterEach(() => {
  vi.restoreAllMocks()
})

function mount(overrides: { onPick?: (path: string) => void } = {}) {
  return render(
    <FolderPickerDialog open onClose={vi.fn()} onPick={overrides.onPick ?? vi.fn()} />,
  )
}

describe('FolderPickerDialog', () => {
  it('打开时加载盘符根视图;点击盘符行下钻', async () => {
    const list = vi
      .spyOn(api.filesystem, 'list')
      .mockResolvedValueOnce({ path: '', parent: null, directories: ['C:\\', 'D:\\'] })
      .mockResolvedValueOnce({ path: 'D:\\', parent: 'D:\\', directories: [] })
    mount()
    expect(await screen.findByTestId('picker-row-C:\\')).toBeInTheDocument()
    // 初始加载 path 缺省(空串)
    expect(list).toHaveBeenCalledWith('')
    fireEvent.click(screen.getByTestId('picker-row-D:\\'))
    await waitFor(() => expect(list).toHaveBeenLastCalledWith('D:\\'))
    expect(screen.getByTestId('folder-picker-dialog')).toBeInTheDocument()
  })

  it('盘符根视图:上一级与选择当前目录均禁用;下钻后可选择并回调', async () => {
    const onPick = vi.fn()
    vi.spyOn(api.filesystem, 'list')
      .mockResolvedValueOnce({ path: '', parent: null, directories: ['C:\\'] })
      .mockResolvedValueOnce({ path: 'C:\\', parent: 'C:\\', directories: ['data'] })
      .mockResolvedValueOnce({ path: 'C:\\data', parent: 'C:\\', directories: [] })
    mount({ onPick })
    await screen.findByTestId('picker-row-C:\\')
    expect(screen.getByRole('button', { name: '选择当前目录' })).toBeDisabled()
    // 下钻两级:C:\ → data
    fireEvent.click(screen.getByTestId('picker-row-C:\\'))
    fireEvent.click(await screen.findByTestId('picker-row-data'))
    await screen.findByText('此目录下没有子目录')
    const choose = screen.getByRole('button', { name: '选择当前目录' })
    expect(choose).toBeEnabled()
    fireEvent.click(choose)
    expect(onPick).toHaveBeenCalledWith('C:\\data')
  })

  it('面包屑「此电脑」可回跳盘符根;上一级按钮跳 parent', async () => {
    vi.spyOn(api.filesystem, 'list')
      .mockResolvedValueOnce({ path: '', parent: null, directories: ['C:\\'] })
      .mockResolvedValueOnce({ path: 'C:\\a', parent: 'C:\\', directories: ['b'] })
      .mockResolvedValueOnce({ path: 'C:\\', parent: 'C:\\', directories: ['a'] })
      .mockResolvedValueOnce({ path: '', parent: null, directories: ['C:\\'] })
    mount()
    await screen.findByTestId('picker-row-C:\\')
    fireEvent.click(screen.getByTestId('picker-row-C:\\'))
    expect(await screen.findByTestId('picker-row-b')).toBeInTheDocument()
    // 上一级 → parent
    fireEvent.click(screen.getByRole('button', { name: '返回' }))
    await waitFor(() => expect(api.filesystem.list).toHaveBeenLastCalledWith('C:\\'))
    // 面包屑「此电脑」→ 盘符根
    fireEvent.click(screen.getByText('此电脑'))
    await waitFor(() => expect(api.filesystem.list).toHaveBeenLastCalledWith(''))
  })

  it('加载失败显示错误与重试;重试重新加载当前目录', async () => {
    const list = vi
      .spyOn(api.filesystem, 'list')
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce({ path: '', parent: null, directories: [] })
    mount()
    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(''))
  })

  it('空目录列表显示空态文案', async () => {
    vi.spyOn(api.filesystem, 'list').mockResolvedValue({
      path: 'C:\\empty',
      parent: 'C:\\',
      directories: [],
    })
    mount()
    expect(await screen.findByText('此目录下没有子目录')).toBeInTheDocument()
  })
})
