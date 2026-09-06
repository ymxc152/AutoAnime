/*
 * DataTable sticky 列:声明 sticky: true 的列,th/td 固定在左侧且背景不透明。
 */
import { render, screen } from '@testing-library/react'
import { DataTable, type Column } from '../DataTable'

interface Row {
  id: number
  name: string
  note: string
}

const columns: Column<Row>[] = [
  { key: 'name', header: '名称', sticky: true, render: (r) => r.name },
  { key: 'note', header: '备注', render: (r) => r.note },
]

const rows: Row[] = [
  { id: 1, name: '葬送的芙莉莲', note: 'ok' },
]

describe('DataTable', () => {
  it('sticky 列的 th/td 固定在左侧且背景不透明', () => {
    render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} />)
    const th = screen.getByText('名称').closest('th')
    expect(th).toHaveClass('sticky', 'left-0', 'bg-surface')
    const td = screen.getByText('葬送的芙莉莲').closest('td')
    expect(td).toHaveClass('sticky', 'left-0', 'bg-surface')
    // 非 sticky 列不受影响
    const thNote = screen.getByText('备注').closest('th')
    expect(thNote).not.toHaveClass('sticky')
  })
})
