import { Fragment } from 'react'

// A small, presentation-only C++/Arduino syntax tokenizer — regex-based
// classification, not a parser. It only ever colors already-rendered
// section text (see BuildMode.jsx); it does not read, own, or alter the
// firmware section model, security-region enforcement, or the editable
// source itself.
const KEYWORDS = new Set([
  'void', 'int', 'float', 'double', 'char', 'long', 'short', 'unsigned', 'signed',
  'bool', 'boolean', 'byte', 'const', 'static', 'volatile', 'struct', 'class', 'enum',
  'typedef', 'union', 'namespace', 'using', 'template', 'public', 'private', 'protected',
  'virtual', 'friend', 'inline', 'explicit', 'new', 'delete', 'sizeof', 'return', 'if',
  'else', 'for', 'while', 'do', 'switch', 'case', 'break', 'continue', 'default', 'goto',
  'true', 'false', 'nullptr', 'NULL', 'try', 'catch', 'throw', 'operator', 'this',
  'extern', 'register', 'auto',
])

const TOKEN_REGEX = new RegExp(
  [
    String.raw`(?<comment>\/\/[^\n]*|\/\*[\s\S]*?\*\/)`,
    String.raw`(?<string>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')`,
    String.raw`(?<include><[A-Za-z_][\w./]*\.(?:h|hpp)>)`,
    String.raw`(?<preproc>#\s*[A-Za-z_]+)`,
    String.raw`(?<number>\b0[xX][0-9a-fA-F]+\b|\b\d+\.\d+[fF]?\b|\b\d+[uUlLfF]*\b)`,
    String.raw`(?<ident>\b[A-Za-z_][A-Za-z0-9_]*\b)`,
  ].join('|'),
  'g',
)

function classifyIdent(word) {
  if (KEYWORDS.has(word)) return 'kw'
  if (word.length > 1 && /^[A-Z][A-Z0-9_]*$/.test(word)) return 'const'
  if (/^[A-Z]/.test(word)) return 'type'
  return null
}

// Renders `code` as a `<pre>` (same role the plain-text segment it replaces
// played) whose children are a mix of plain strings and classed `<span>`s —
// never `dangerouslySetInnerHTML`, so this carries no injection risk beyond
// what rendering the same text as a plain string already had.
export default function CppCode({ code, className }) {
  const text = code ?? ''
  const nodes = []
  let lastIndex = 0
  let key = 0
  // `matchAll` iterates its own internal copy of `TOKEN_REGEX`, so the
  // shared module-level regex is never mutated here (no `lastIndex`
  // read/write on it) — a plain `exec` loop would otherwise require exactly
  // that mutation.
  for (const match of text.matchAll(TOKEN_REGEX)) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index))
    const { comment, string, include, preproc, number, ident } = match.groups
    if (comment) {
      nodes.push(
        <span className="cpp-comment" key={key++}>
          {comment}
        </span>,
      )
    } else if (string) {
      nodes.push(
        <span className="cpp-string" key={key++}>
          {string}
        </span>,
      )
    } else if (include) {
      nodes.push(
        <span className="cpp-include" key={key++}>
          {include}
        </span>,
      )
    } else if (preproc) {
      const [, hash, directive] = preproc.match(/^(#\s*)([A-Za-z_]+)$/)
      nodes.push(
        <Fragment key={key++}>
          {hash}
          <span className="cpp-kw">{directive}</span>
        </Fragment>,
      )
    } else if (number) {
      nodes.push(
        <span className="cpp-number" key={key++}>
          {number}
        </span>,
      )
    } else if (ident) {
      const kind = classifyIdent(ident)
      nodes.push(
        kind ? (
          <span className={`cpp-${kind}`} key={key++}>
            {ident}
          </span>
        ) : (
          ident
        ),
      )
    }
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex))
  return <pre className={className}>{nodes}</pre>
}
