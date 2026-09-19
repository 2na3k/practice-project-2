import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  devIndicators: false,
  serverExternalPackages: [
    "@earendil-works/pi-ai",
    "@earendil-works/pi-agent-core",
    "@modelcontextprotocol/client",
    "@langfuse/tracing",
    "@langfuse/otel",
    "@opentelemetry/sdk-node",
  ],
};
export default nextConfig;
