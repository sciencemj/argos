import type { ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

// Agent answers (and notes from Phase 8): Markdown in the app's type scale, no raw HTML.
const components: Components = {
  p: ({ children }) => <p className="m-0 [&+*]:mt-2">{children}</p>,
  strong: ({ children }) => (
    <strong className="font-medium text-ink">{children}</strong>
  ),
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="underline underline-offset-[3px]"
    >
      {children}
    </a>
  ),
  ul: ({ children }) => <ul className="my-2 list-disc pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal pl-5">{children}</ol>,
  li: ({ children }) => <li className="my-0.5">{children}</li>,
  code: ({ children, className }) =>
    className ? (
      <code className={`${className} font-mono text-[12.5px]`}>{children}</code>
    ) : (
      <code className="rounded-md bg-inset px-[5px] py-px font-mono text-[12.5px] text-text">
        {children}
      </code>
    ),
  pre: ({ children }) => (
    <pre className="my-2 overflow-x-auto rounded-xl bg-inset p-3 text-[12.5px]">
      {children}
    </pre>
  ),
  h1: ({ children }) => (
    <p className="m-0 mt-2 font-medium text-ink">{children}</p>
  ),
  h2: ({ children }) => (
    <p className="m-0 mt-2 font-medium text-ink">{children}</p>
  ),
  h3: ({ children }) => (
    <p className="m-0 mt-2 font-medium text-ink">{children}</p>
  ),
  table: ({ children }) => (
    <table className="my-2 border-collapse text-[12.5px] [&_td]:border [&_td]:border-line-soft [&_td]:px-2 [&_th]:border [&_th]:border-line-soft [&_th]:px-2">
      {children}
    </table>
  ),
};

/** A link or picture shown as plain text, so nothing is fetched or opened. */
function plain(label: ReactNode, url: string | undefined) {
  const same = typeof label === "string" && label === url;
  return (
    <span className="break-all">
      {label}
      {url && !same && <span className="text-meta">{` (${url})`}</span>}
    </span>
  );
}

// Private answers stay on this device: an image would load from (and a link could
// send to) whatever host the model wrote, carrying the private text in its address.
const offline: Components = {
  ...components,
  a: ({ children, href }) => plain(children, href),
  img: ({ alt, src }) =>
    plain(alt || null, typeof src === "string" ? src : undefined),
};

export function Markdown({
  text,
  offline: noFetch = false,
}: {
  text: string;
  /** Images and links as plain text (the private chat). */
  offline?: boolean;
}) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={noFetch ? offline : components}
    >
      {text}
    </ReactMarkdown>
  );
}
