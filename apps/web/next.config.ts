import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  transpilePackages: ["@ecommerce-agent-system/ui"],
};

export default nextConfig;
