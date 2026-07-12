/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Allow screenshots served from the backend's /artifacts mount.
  images: { remotePatterns: [{ protocol: "http", hostname: "localhost" }] },
};

export default nextConfig;
