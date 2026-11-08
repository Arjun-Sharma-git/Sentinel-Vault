// Generic data table used by the Resources and Audit pages.

import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table'
import { ArrowDown, ArrowUp, ArrowUpDown, Search } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { cn } from './ui'

/**
 * A sortable, searchable table (TanStack Table). Clicking a row selects it; the selected row can
 * render an expanded panel underneath via `renderExpanded`.
 */
export function DataTable<T>({
  data,
  columns,
  onRowClick,
  selectedId,
  getRowId,
  initialSorting = [],
  searchPlaceholder = 'Search…',
  toolbar,
  renderExpanded,
}: {
  data: T[]
  // `any` is TanStack's own convention for a list of columns with different value types.
  columns: ColumnDef<T, any>[]
  onRowClick?: (row: T) => void
  selectedId?: string | null
  getRowId: (row: T) => string
  initialSorting?: SortingState
  searchPlaceholder?: string
  toolbar?: ReactNode
  renderExpanded?: (row: T) => ReactNode
}) {
  const [sorting, setSorting] = useState<SortingState>(initialSorting)
  const [globalFilter, setGlobalFilter] = useState('')

  const table = useReactTable({
    data,
    columns,
    state: { sorting, globalFilter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    getRowId: (row) => getRowId(row),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  })

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-6 py-4">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3.5 top-1/2 size-3.5 -translate-y-1/2 text-muted" />
          <input
            value={globalFilter}
            onChange={(e) => setGlobalFilter(e.target.value)}
            placeholder={searchPlaceholder}
            className="w-72 max-w-full rounded-full border border-line bg-cream py-2 pl-9 pr-4 text-sm outline-none transition-colors placeholder:text-muted/70 focus:border-gold focus:bg-paper"
          />
        </div>
        {toolbar}
        <span className="ml-auto font-mono text-xs text-muted">
          {table.getFilteredRowModel().rows.length} of {data.length}
        </span>
      </div>
      <div className="overflow-x-auto scrollbar-thin">
        <table className="w-full text-sm">
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id} className="border-b border-line">
                {hg.headers.map((h) => {
                  const sorted = h.column.getIsSorted()
                  return (
                    <th
                      key={h.id}
                      className="whitespace-nowrap px-6 py-3 text-left text-[10px] font-medium uppercase tracking-[0.18em] text-muted"
                    >
                      {h.isPlaceholder ? null : h.column.getCanSort() ? (
                        <button
                          onClick={h.column.getToggleSortingHandler()}
                          className="inline-flex items-center gap-1 uppercase tracking-[0.18em] hover:text-ink"
                        >
                          {flexRender(h.column.columnDef.header, h.getContext())}
                          {sorted === 'asc' ? (
                            <ArrowUp className="size-3" />
                          ) : sorted === 'desc' ? (
                            <ArrowDown className="size-3" />
                          ) : (
                            <ArrowUpDown className="size-3 opacity-40" />
                          )}
                        </button>
                      ) : (
                        flexRender(h.column.columnDef.header, h.getContext())
                      )}
                    </th>
                  )
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => {
              const selected = selectedId === row.id
              return [
                <tr
                  key={row.id}
                  onClick={() => onRowClick?.(row.original)}
                  className={cn(
                    'border-b border-line/70 transition-colors',
                    onRowClick && 'cursor-pointer hover:bg-cream/70',
                    selected && 'bg-gold-soft/60',
                  )}
                >
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className="px-6 py-3.5 align-middle">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>,
                selected && renderExpanded ? (
                  <tr key={`${row.id}-x`} className="border-b border-line/70 bg-cream/60">
                    <td colSpan={row.getVisibleCells().length} className="px-6 py-4">
                      {renderExpanded(row.original)}
                    </td>
                  </tr>
                ) : null,
              ]
            })}
          </tbody>
        </table>
        {table.getRowModel().rows.length === 0 && (
          <div className="px-6 py-12 text-center font-serif text-xl text-muted">Nothing matches.</div>
        )}
      </div>
    </div>
  )
}
