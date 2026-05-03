import ReactMarkdown from 'react-markdown'

interface Props {
  filename: string
  content: string
}

function parseCSV(raw: string): { headers: string[]; rows: string[][] } {
  const lines = raw.trim().split('\n')
  const parse = (line: string): string[] => {
    const cells: string[] = []
    let cur = ''
    let inQuote = false
    for (let i = 0; i < line.length; i++) {
      const ch = line[i]
      if (ch === '"') {
        if (inQuote && line[i + 1] === '"') { cur += '"'; i++ }
        else inQuote = !inQuote
      } else if (ch === ',' && !inQuote) {
        cells.push(cur); cur = ''
      } else {
        cur += ch
      }
    }
    cells.push(cur)
    return cells
  }
  const [headerLine, ...dataLines] = lines
  return {
    headers: parse(headerLine),
    rows: dataLines.filter(l => l.trim()).map(parse),
  }
}

function CSVTable({ content }: { content: string }) {
  const { headers, rows } = parseCSV(content)
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-xs border-collapse">
        <thead>
          <tr className="border-b border-gray-700">
            {headers.map((h, i) => (
              <th key={i} className="px-3 py-2 text-left text-gray-400 font-semibold whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} className="border-b border-gray-800/60 hover:bg-gray-800/40">
              {headers.map((_, ci) => (
                <td key={ci} className="px-3 py-1.5 text-gray-300 align-top">
                  {row[ci] ?? ''}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function JSONViewer({ content }: { content: string }) {
  let formatted = content
  try { formatted = JSON.stringify(JSON.parse(content), null, 2) } catch { /* leave raw */ }
  return (
    <pre className="text-xs text-gray-300 whitespace-pre-wrap leading-relaxed overflow-x-auto">
      {formatted}
    </pre>
  )
}

export default function FileViewer({ filename, content }: Props) {
  const ext = filename.split('.').pop()?.toLowerCase()

  if (ext === 'csv') {
    return <CSVTable content={content} />
  }

  if (ext === 'md') {
    return (
      <div className="prose prose-invert prose-sm max-w-none text-gray-300 leading-relaxed
        prose-headings:text-gray-100 prose-headings:font-semibold
        prose-a:text-blue-400 prose-strong:text-gray-100
        prose-code:text-green-400 prose-code:bg-gray-800 prose-code:px-1 prose-code:rounded
        prose-pre:bg-gray-800 prose-pre:border prose-pre:border-gray-700
        prose-blockquote:border-gray-600 prose-blockquote:text-gray-400
        prose-li:text-gray-300 prose-hr:border-gray-700">
        <ReactMarkdown>{content}</ReactMarkdown>
      </div>
    )
  }

  if (ext === 'json') {
    return <JSONViewer content={content} />
  }

  return (
    <pre className="text-xs text-gray-300 whitespace-pre-wrap leading-relaxed overflow-x-auto">
      {content}
    </pre>
  )
}
