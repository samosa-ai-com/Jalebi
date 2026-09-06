import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const COMPONENTS: Components = {
  pre: ({ children }) => (
    <pre className="my-3 max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-ink-900/60 p-3 font-mono text-xs leading-relaxed text-ink-200">
      {children}
    </pre>
  ),
  code: ({ className, children, node }) => {
    // react-markdown v9+ drops the `inline` prop on code; detect block code via
    // the language className OR the multiline heuristic (start.line !== end.line).
    const pos = node?.position;
    const isBlock =
      /language-[\w-]+/.test(className ?? "") ||
      Boolean(pos && pos.start.line !== pos.end.line);
    if (isBlock) {
      return (
        <code className={className}>
          {String(children).replace(/\n$/, "")}
        </code>
      );
    }
    return (
      <code className="rounded bg-ink-850 px-1.5 py-0.5 font-mono text-[0.9em] text-syrup-300">
        {children}
      </code>
    );
  },
  h1: ({ children }) => (
    <h1 className="mt-4 mb-2 text-lg font-bold tracking-tight text-ink-100">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="mt-4 mb-2 text-base font-semibold text-ink-100">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="mt-3 mb-1 text-sm font-semibold text-syrup-300">{children}</h3>
  ),
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-syrup-400 underline-offset-4 hover:text-syrup-300 hover:underline"
    >
      {children}
    </a>
  ),
  table: ({ children }) => (
    <table className="my-3 w-full border-collapse text-sm text-ink-300">{children}</table>
  ),
  tr: ({ children }) => <tr className="border-b border-ink-800">{children}</tr>,
  th: ({ children }) => (
    <th className="border-b border-ink-800 px-3 py-2 font-mono text-xs text-ink-400">{children}</th>
  ),
  td: ({ children }) => <td className="px-3 py-2 align-top">{children}</td>,
  ul: ({ children }) => <ul className="my-2 list-disc pl-5 space-y-1">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal pl-5 space-y-1">{children}</ol>,
  li: ({ children }) => <li>{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="my-3 border-l-2 border-syrup-500/50 pl-4 text-ink-400">{children}</blockquote>
  ),
  strong: ({ children }) => <strong className="font-semibold text-ink-100">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
};

export default function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={className}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
