import type { NextConfig } from "next";

/**
 * Static export → S3 + CloudFront. No SSR, no edge runtime.
 * `trailingSlash` keeps S3 routing simple (every page is `<route>/index.html`).
 */
const config: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: {
    unoptimized: true,
  },
  reactStrictMode: true,
  poweredByHeader: false,
  productionBrowserSourceMaps: true,
};

export default config;
