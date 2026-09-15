import { Client, StreamableHTTPClientTransport, type CallToolResult } from "@modelcontextprotocol/client";

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
  const client = new Client({ name: "remymy-webapp", version: "0.1.0" });
  const transport = new StreamableHTTPClientTransport(mcpEndpoint());
  try {
    await client.connect(transport);
    const result = await client.callTool(
      { name, arguments: args },
      { requestSignal: signal },
    );
    const text = textFromToolResult(result);
    if (result.isError || !text) throw new McpClientError(`MCP tool '${name}' could not complete.`);
    return text;
  } catch (error) {
    if (error instanceof McpClientError) throw error;
    throw new McpClientError("The Python MCP service is unavailable.");
  } finally {
    await transport.terminateSession().catch(() => undefined);
    await client.close().catch(() => undefined);
  }
}
