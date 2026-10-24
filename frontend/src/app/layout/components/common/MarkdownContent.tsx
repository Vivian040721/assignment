import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { desktopApi } from '../../../services/desktopApi';
import { workspaceStore } from '../../../stores/workspaceStore';
import { safeHttpUrl } from '../../../utils/format';

export function MarkdownContent({ text }: { text: string }) {
  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a: ({ href, children }) => {
            const url = safeHttpUrl(href);
            if (!url) return <span>{children}</span>;
            return <button className="markdown-link" type="button" onClick={() => void openExternal(url)}>{children}</button>;
          },
          img: () => null,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

async function openExternal(url: string): Promise<void> {
  try {
    await desktopApi().openExternal(url);
  } catch (error) {
    workspaceStore.setError(error instanceof Error ? error.message : String(error));
  }
}
