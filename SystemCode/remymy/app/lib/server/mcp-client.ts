import { Client, StreamableHTTPClientTransport, type CallToolResult } from "@modelcontextprotocol/client";
import { withLangfuseSpan } from "./langfuse";

export class McpClientError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "McpClientError";
  }
}

function mcpEndpoint(): URL {
  const configured = process.env.MCP_URL?.trim() || "http://localhost:8000/mcp";
  const endpoint = configured.endsWith("/mcp") ? configured : `${configured.replace(/\/$/, "")}/mcp`;
  return new URL(endpoint);
}

function textFromToolResult(result: CallToolResult): string {
  return result.content
    .filter((item): item is { type: "text"; text: string } => item.type === "text")
    .map((item) => item.text)
    .join("\n")
    .trim();
}

export async function callMcpTool(
  name: string,
  args: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<string> {
  const endpoint = mcpEndpoint();
  return withLangfuseSpan("mcp.client.call", async (span) => {
    const startedAt = performance.now();
    span.update({
      input: { tool: name, arguments: args },
      metadata: { protocol: "mcp", transport: "streamable-http", endpoint: `${endpoint.host}${endpoint.pathname}` },
    });
    const client = new Client({ name: "remymy-webapp", version: "0.1.0" });
    const transport = new StreamableHTTPClientTransport(endpoint);
    try {
      await client.connect(transport);
      const result = await client.callTool(
        { name, arguments: args },
        { requestSignal: signal },
      );
      const text = textFromToolResult(result);
      span.update({
        output: {
          isError: Boolean(result.isError),
          content: result.content,
          structuredContent: result.structuredContent ?? null,
        },
        metadata: {
          protocol: "mcp",
          transport: "streamable-http",
          endpoint: `${endpoint.host}${endpoint.pathname}`,
          durationMs: Math.round((performance.now() - startedAt) * 10) / 10,
        },
        ...(result.isError || !text ? { level: "ERROR", statusMessage: `MCP tool '${name}' returned no usable result.` } : {}),
      });
      if (result.isError || !text) throw new McpClientError(`MCP tool '${name}' could not complete.`);
      return text;
    } catch (error) {
      span.update({
        level: "ERROR",
        statusMessage: error instanceof Error ? error.message : "The MCP call failed.",
        metadata: {
          protocol: "mcp",
          transport: "streamable-http",
          endpoint: `${endpoint.host}${endpoint.pathname}`,
          durationMs: Math.round((performance.now() - startedAt) * 10) / 10,
        },
      });
      if (error instanceof McpClientError) throw error;
      throw new McpClientError("The Python MCP service is unavailable.");
    } finally {
      await transport.terminateSession().catch(() => undefined);
      await client.close().catch(() => undefined);
    }
  }, { asType: "tool" });
}
