import os

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse


mcp = MCPServer("Remymy MCP")


@mcp.tool()
def say_hello(name: str = "there") -> str:
    """Say hello to someone."""
    clean_name = name.strip() or "there"
    return f"Hello, {clean_name}! Welcome to Remymy."


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )
