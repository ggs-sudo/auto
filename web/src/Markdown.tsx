// Agent-written text, rendered. Gate questions arrive as Markdown — bold
// sub-questions, numbered hand-off steps, fenced commands — and shown raw
// they were a wall of asterisks and backticks. Raw HTML in the source is
// escaped rather than rendered, and links keep only web schemes: the text
// comes from a session, not from us.

import { Marked, type Tokens } from "marked";

const escapeHtml = (s: string) =>
  s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

const marked = new Marked({
  gfm: true,
  renderer: {
    html({ text }: Tokens.HTML | Tokens.Tag) {
      return escapeHtml(text);
    },
    link({ href, title, tokens }: Tokens.Link) {
      const label = this.parser.parseInline(tokens);
      if (!/^https?:\/\//i.test(href)) return label;
      const t = title ? ` title="${escapeHtml(title)}"` : "";
      return `<a href="${escapeHtml(href)}"${t} target="_blank" rel="noreferrer">${label}</a>`;
    },
  },
});

export function Markdown({ text, inline = false, className }: {
  text: string;
  inline?: boolean;
  className?: string;
}) {
  const html = inline
    ? (marked.parseInline(text, { async: false }) as string)
    : (marked.parse(text, { async: false }) as string);
  const Tag = inline ? "span" : "div";
  return <Tag className={className} dangerouslySetInnerHTML={{ __html: html }} />;
}
