import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

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

const mdComponents: React.ComponentProps<typeof ReactMarkdown>['components'] = {
  h1: ({ children }) => <h1 className="text-2xl font-bold text-gray-100 mt-6 mb-3 border-b border-gray-800 pb-1">{children}</h1>,
  h2: ({ children }) => <h2 className="text-xl font-semibold text-gray-100 mt-5 mb-2">{children}</h2>,
  h3: ({ children }) => <h3 className="text-base font-semibold text-gray-200 mt-4 mb-1.5 uppercase tracking-wide">{children}</h3>,
  h4: ({ children }) => <h4 className="text-sm font-semibold text-gray-200 mt-3 mb-1">{children}</h4>,
  p: ({ children }) => <p className="text-gray-300 leading-relaxed mb-3">{children}</p>,
  ul: ({ children }) => <ul className="list-disc list-outside pl-5 text-gray-300 space-y-1 mb-3">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal list-outside pl-5 text-gray-300 space-y-1 mb-3">{children}</ol>,
  li: ({ children }) => <li className="text-gray-300 leading-relaxed">{children}</li>,
  strong: ({ children }) => <strong className="text-gray-100 font-semibold">{children}</strong>,
  em: ({ children }) => <em className="italic text-gray-300">{children}</em>,
  code: ({ children }) => <code className="text-green-400 bg-gray-800 px-1 py-0.5 rounded text-sm font-mono">{children}</code>,
  pre: ({ children }) => <pre className="bg-gray-800 border border-gray-700 rounded-md p-4 overflow-x-auto text-sm mb-3 leading-relaxed">{children}</pre>,
  blockquote: ({ children }) => <blockquote className="border-l-2 border-gray-600 pl-4 text-gray-400 italic mb-3">{children}</blockquote>,
  hr: () => <hr className="border-gray-700 my-5" />,
  a: ({ href, children }) => <a href={href} className="text-blue-400 hover:underline" target="_blank" rel="noopener noreferrer">{children}</a>,
  table: ({ children }) => <div className="overflow-x-auto mb-3"><table className="min-w-full text-sm border-collapse">{children}</table></div>,
  thead: ({ children }) => <thead className="border-b border-gray-700">{children}</thead>,
  th: ({ children }) => <th className="px-3 py-2 text-left text-gray-300 font-semibold">{children}</th>,
  td: ({ children }) => <td className="px-3 py-2 text-gray-400 border-b border-gray-800/60 align-top">{children}</td>,
}

export function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="text-sm">
      <ReactMarkdown components={mdComponents} remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  )
}

const chatMdComponents: React.ComponentProps<typeof ReactMarkdown>['components'] = {
  h1: ({ children }) => <h1 className="text-xl font-bold text-gray-100 mt-4 mb-2 border-b border-gray-700 pb-1">{children}</h1>,
  h2: ({ children }) => <h2 className="text-lg font-semibold text-gray-100 mt-4 mb-2">{children}</h2>,
  h3: ({ children }) => <h3 className="text-base font-semibold text-gray-200 mt-3 mb-1.5">{children}</h3>,
  h4: ({ children }) => <h4 className="text-sm font-semibold text-gray-200 mt-2 mb-1">{children}</h4>,
  p: ({ children }) => <p className="text-gray-300 leading-relaxed mb-2 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="list-disc list-outside pl-5 text-gray-300 space-y-1 mb-2">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal list-outside pl-5 text-gray-300 space-y-1 mb-2">{children}</ol>,
  li: ({ children }) => <li className="text-gray-300 leading-relaxed">{children}</li>,
  strong: ({ children }) => <strong className="text-gray-100 font-semibold">{children}</strong>,
  em: ({ children }) => <em className="italic text-gray-300">{children}</em>,
  code: ({ children }) => <code className="text-green-400 bg-gray-900 px-1 py-0.5 rounded text-xs font-mono">{children}</code>,
  pre: ({ children }) => <pre className="bg-gray-900 border border-gray-700 rounded-md p-3 overflow-x-auto text-xs mb-2 leading-relaxed">{children}</pre>,
  blockquote: ({ children }) => <blockquote className="border-l-2 border-gray-600 pl-3 text-gray-400 italic mb-2">{children}</blockquote>,
  hr: () => <hr className="border-gray-700 my-4" />,
  a: ({ href, children }) => <a href={href} className="text-blue-400 hover:underline" target="_blank" rel="noopener noreferrer">{children}</a>,
  table: ({ children }) => <div className="overflow-x-auto mb-2"><table className="min-w-full text-xs border-collapse">{children}</table></div>,
  thead: ({ children }) => <thead className="border-b border-gray-700">{children}</thead>,
  th: ({ children }) => <th className="px-3 py-1.5 text-left text-gray-300 font-semibold">{children}</th>,
  td: ({ children }) => <td className="px-3 py-1.5 text-gray-400 border-b border-gray-800/60 align-top">{children}</td>,
}

export function ChatMarkdownContent({ content }: { content: string }) {
  return (
    <div className="text-sm break-words">
      <ReactMarkdown components={chatMdComponents} remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  )
}

export default function FileViewer({ filename, content }: Props) {
  const ext = filename.split('.').pop()?.toLowerCase()

  if (ext === 'csv') return <CSVTable content={content} />
  if (ext === 'md') return <MarkdownContent content={content} />
  if (ext === 'json') return <JSONViewer content={content} />

  return (
    <pre className="text-xs text-gray-300 whitespace-pre-wrap leading-relaxed overflow-x-auto">
      {content}
    </pre>
  )
}
