import ReactMarkdown from "react-markdown";

interface Props {
  source: string;
  className?: string;
}

/**
 * Markdown renderer for user-supplied text (model descriptions, etc.).
 *
 * Safe by default: react-markdown does not interpret raw HTML unless
 * configured with `rehype-raw` (which we don't use). XSS-safe.
 */
export function MarkdownView({ source, className }: Props) {
  return (
    <div className={className}>
      <ReactMarkdown>{source}</ReactMarkdown>
    </div>
  );
}
