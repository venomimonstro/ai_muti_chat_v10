"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

function safeUrl(url: string) {
  try {
    const parsed = new URL(url, window.location.origin);
    if (!["http:", "https:", "mailto:"].includes(parsed.protocol)) return "";
    return url;
  } catch {
    return "";
  }
}

export function MarkdownMessage({content}: {content: string}) {
  return (
    <div className="markdownMessage">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        urlTransform={safeUrl}
        components={{
          a: ({children, href}) => (
            <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>
          ),
          pre: ({children}) => <pre className="codeBlock">{children}</pre>,
          code: ({children, className}) => <code className={className}>{children}</code>,
          table: ({children}) => <div className="tableScroll"><table>{children}</table></div>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
