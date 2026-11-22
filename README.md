# new

## MarkItDown

The [MarkItDown](https://github.com/microsoft/markitdown) MCP server is wired up in `.mcp.json`.
Claude Code picks it up automatically when it opens this project (approve the `markitdown` server when asked).

It provides the `convert_to_markdown(uri)` tool, which converts `http:`, `https:`, `file:` and `data:` URIs
(PDF, DOCX, PPTX, XLSX, HTML, images, audio, YouTube, etc.) to Markdown.

Requirement: [`uv`](https://docs.astral.sh/uv/) (`uvx` is used to run `markitdown-mcp`).

Command-line use without MCP:

```bash
uvx markitdown path/to/file.pdf > file.md
```
