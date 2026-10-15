import type { NextConfig } from "next";

// Домены, с которых разрешены dev-ресурсы Next (HMR): нужны, когда dev-сервер открывают
// не по localhost, а через туннель/превью-хост. Список задаётся через окружение и
// поддерживает маски: NEXT_DEV_ALLOWED_ORIGINS="*.e2b.app,local-origin.dev".
const devOrigins = (process.env.NEXT_DEV_ALLOWED_ORIGINS ?? "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

const nextConfig: NextConfig = {
  // standalone-сборка для лёгкого Docker-образа (self-hosting)
  output: "standalone",
  allowedDevOrigins: devOrigins,
};

export default nextConfig;
