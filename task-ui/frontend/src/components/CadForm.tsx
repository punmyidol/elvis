import { useState } from 'react'

interface Props {
  onGenerate: (prompt: string) => void
  disabled: boolean
}

const EXAMPLES = [
  'a 20×20×10 mm solid box',
  'a cylinder 30 mm diameter, 50 mm tall with a 10 mm axial hole',
  'an L-bracket 40×40×3 mm with two M4 mounting holes',
]

export default function CadForm({ onGenerate, disabled }: Props) {
  const [prompt, setPrompt] = useState('')

  const handleSubmit = () => {
    if (!prompt.trim() || disabled) return
    onGenerate(prompt.trim())
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') handleSubmit()
  }

  return (
    <section className="space-y-3">
      <h2 className="text-xs font-medium text-gray-500 uppercase tracking-widest">New Model</h2>
      <textarea
        className="w-full bg-gray-900 border border-gray-800 rounded-lg px-4 py-3 text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:border-gray-600 resize-none"
        rows={3}
        placeholder="Describe the 3D part you want to generate…"
        value={prompt}
        onChange={e => setPrompt(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
      />
      <div className="flex items-center gap-4 flex-wrap">
        <button
          onClick={handleSubmit}
          disabled={disabled || !prompt.trim()}
          className="px-4 py-2 bg-white text-gray-950 text-sm font-semibold rounded-lg disabled:opacity-30 hover:bg-gray-200 transition-colors"
        >
          {disabled ? 'Generating…' : 'Generate'}
        </button>
        <span className="text-xs text-gray-600">⌘ + Enter</span>
      </div>
      <div className="flex flex-wrap gap-2 pt-1">
        {EXAMPLES.map(ex => (
          <button
            key={ex}
            onClick={() => setPrompt(ex)}
            disabled={disabled}
            className="px-2.5 py-1 text-xs text-gray-500 border border-gray-800 rounded-md hover:border-gray-600 hover:text-gray-300 transition-colors disabled:opacity-30"
          >
            {ex}
          </button>
        ))}
      </div>
    </section>
  )
}
