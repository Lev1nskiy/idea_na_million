import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // standalone-сборка для лёгкого Docker-образа (self-hosting)
  output: "standalone",
};

export default nextConfig;
