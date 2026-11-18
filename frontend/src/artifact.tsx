import { useEffect, useId, useRef, useState } from "react";
import { tr } from "./i18n";
import { useThemePref } from "./theme";

/** Inline visuals an agent made with render_artifact (v0.2). Everything renders inside
 * an <iframe sandbox> WITHOUT allow-same-origin, so the code cannot reach Argos (its
 * cookies, API or DOM), plus a CSP with no network at all:
 * - html / tsx: sandbox="allow-scripts" and a script-allowing CSP; tsx is transpiled
 *   here with sucrase and runs on an inlined Preact (React-compatible hooks).
 * - svg / mermaid: sandbox="" (no scripts at all); mermaid is rendered to SVG here first
 *   with securityLevel "strict". */

export type ArtifactKind = "html" | "svg" | "mermaid" | "tsx";

const CSP_SCRIPTS =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; " +
  "img-src data: blob:; font-src data:; connect-src 'none'";
const CSP_STATIC =
  "default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:";

// The frame reports its height to the parent (the only channel it has).
const HEIGHT_SCRIPT = `<script>(function(){var last=0;function post(){var h=Math.ceil(document.body.getBoundingClientRect().height);if(h!==last){last=h;parent.postMessage({argosArtifactHeight:h},"*")}}new ResizeObserver(post).observe(document.body);addEventListener("load",post);post()})()</script>`;

function page(csp: string, body: string, dark: boolean, scripts = ""): string {
  const colors = dark
    ? "color-scheme:dark;background:#1c1b22;color:#e8e6ee"
    : "color-scheme:light;background:#fff;color:#1f1d24";
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${csp}"><style>html,body{margin:0;padding:12px;${colors};font-family:-apple-system,system-ui,sans-serif;font-size:14px}svg{max-width:100%;height:auto}</style></head><body>${body}${scripts}</body></html>`;
}

const scriptSafe = (code: string) => code.replace(/<\/script/gi, "<\\/script");

async function tsxPage(code: string, dark: boolean): Promise<string> {
  const [{ transform }, preact, hooks] = await Promise.all([
    import("sucrase"),
    import("../node_modules/preact/dist/preact.umd.js?raw"),
    import("../node_modules/preact/hooks/dist/hooks.umd.js?raw"),
  ]);
  const compiled = transform(code, {
    transforms: ["typescript", "jsx", "imports"],
    jsxPragma: "h",
    jsxFragmentPragma: "Fragment",
    production: true,
  }).code;
  const runtime = `<script>${scriptSafe(preact.default)}</script><script>${scriptSafe(hooks.default)}</script><script>
var React=Object.assign({},preact,preactHooks,{createElement:preact.h});React.default=React;
var h=preact.h,Fragment=preact.Fragment;
function require(n){if(n==="react"||n==="preact"||n==="react-dom"||n==="preact/hooks"||n==="react/jsx-runtime")return React;throw new Error("Only React can be imported here: "+n)}
var exports={},module={exports:exports};
try{(function(){${scriptSafe(compiled)}})();var App=module.exports.default||exports.default||exports.App;
if(typeof App!=="function")throw new Error("export default a component (function App)");
preact.render(h(App,null),document.getElementById("root"))}catch(e){document.body.innerHTML="<pre style='color:#c33;white-space:pre-wrap'>"+String(e&&e.stack||e).replace(/</g,"&lt;")+"</pre>"}
</script>`;
  return page(
    CSP_SCRIPTS,
    '<div id="root"></div>',
    dark,
    runtime + HEIGHT_SCRIPT,
  );
}

let mermaidReady: Promise<typeof import("mermaid").default> | null = null;

async function mermaidSvg(
  code: string,
  id: string,
  dark: boolean,
): Promise<string> {
  mermaidReady ??= import("mermaid").then((m) => m.default);
  const mermaid = await mermaidReady;
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: dark ? "dark" : "default",
  });
  const { svg } = await mermaid.render(
    `artifact-${id.replace(/[^a-zA-Z0-9]/g, "")}`,
    code,
  );
  return svg;
}

async function build(
  kind: ArtifactKind,
  code: string,
  id: string,
  dark: boolean,
): Promise<{ doc: string; scripts: boolean }> {
  switch (kind) {
    case "html":
      return {
        doc: page(CSP_SCRIPTS, code, dark, HEIGHT_SCRIPT),
        scripts: true,
      };
    case "tsx":
      return { doc: await tsxPage(code, dark), scripts: true };
    case "svg":
      return { doc: page(CSP_STATIC, code, dark), scripts: false };
    case "mermaid":
      return {
        doc: page(CSP_STATIC, await mermaidSvg(code, id, dark), dark),
        scripts: false,
      };
  }
}

export function ArtifactView({
  title,
  kind,
  code,
}: {
  title: string;
  kind: ArtifactKind;
  code: string;
}) {
  const id = useId();
  const pref = useThemePref();
  const dark =
    pref === "dark" ||
    (pref === "system" &&
      typeof matchMedia === "function" &&
      matchMedia("(prefers-color-scheme: dark)").matches);
  const frame = useRef<HTMLIFrameElement>(null);
  const [doc, setDoc] = useState<{ doc: string; scripts: boolean } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState(false);
  const [height, setHeight] = useState(
    kind === "svg" || kind === "mermaid" ? 360 : 240,
  );

  useEffect(() => {
    let live = true;
    setError(null);
    build(kind, code, id, dark).then(
      (built) => live && setDoc(built),
      (e: unknown) =>
        live && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      live = false;
    };
  }, [kind, code, id, dark]);

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      // Only our own frame, and only a number: nothing else crosses the sandbox.
      if (e.source !== frame.current?.contentWindow) return;
      const h = (e.data as { argosArtifactHeight?: unknown } | null)
        ?.argosArtifactHeight;
      if (typeof h === "number" && Number.isFinite(h))
        setHeight(Math.max(80, Math.min(900, h)));
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  return (
    <figure className="m-0 flex flex-col overflow-hidden rounded-2xl border border-line-soft bg-card">
      <figcaption className="flex items-center gap-2 border-b border-line-soft px-3 py-2">
        <span className="min-w-0 grow truncate text-[13px] font-medium text-ink">
          {title}
        </span>
        <span className="rounded-full bg-inset px-2 py-px font-mono text-[10.5px] text-meta">
          {kind}
        </span>
        <button
          type="button"
          aria-pressed={source}
          onClick={() => setSource(!source)}
          className="h-7 cursor-pointer rounded-full px-2.5 text-[12px] text-text-3 hover:text-ink aria-pressed:bg-inset aria-pressed:text-ink"
        >
          {source ? tr("미리보기", "Preview") : tr("소스 보기", "View source")}
        </button>
      </figcaption>
      {source ? (
        <pre className="m-0 max-h-96 overflow-auto bg-inset p-3 font-mono text-[11.5px] leading-[1.55] whitespace-pre-wrap text-text-2 select-text">
          {code}
        </pre>
      ) : error ? (
        <p role="alert" className="m-0 p-3 text-[12.5px] text-danger">
          {tr("그리지 못했어요", "Could not render")}: {error}
        </p>
      ) : doc ? (
        <iframe
          ref={frame}
          title={title}
          // Never allow-same-origin: the code must not reach Argos.
          sandbox={doc.scripts ? "allow-scripts" : ""}
          srcDoc={doc.doc}
          referrerPolicy="no-referrer"
          className="w-full resize-y border-0 bg-card"
          style={{ height }}
        />
      ) : (
        <div className="h-24 animate-pulse bg-inset" aria-busy="true" />
      )}
    </figure>
  );
}
