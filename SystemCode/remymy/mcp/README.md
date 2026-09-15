# Remymy MCP gateway

This is the small Python MCP service used by the Remymy web app. It exposes one
tool over stateless Streamable HTTP:

- `say_hello(name: str = "there")` — returns a friendly greeting.

The MCP endpoint is `http://localhost:8000/mcp` when the service is running
locally. The health endpoint is `http://localhost:8000/health`.

## Run locally

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python main.py
```

The Docker Compose app is configured to call the same endpoint at
`http://mcp:8000/mcp`.
