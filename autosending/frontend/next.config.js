/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  trailingSlash: true,

  // Produces a self-contained server in .next/standalone — used by the prod Docker stage.
  // Has no effect on `next dev`, so dev and prod share the same config file.
  output: "standalone",

  // Dev: set to "/spam" via NEXT_PUBLIC_BASE_PATH env var so the app serves under
  // /spam/ when behind the unified ngrok/nginx dev contour.
  // Prod: unset (empty string = no prefix), app serves at spam.gramly.tech root.
  basePath: process.env.NEXT_PUBLIC_BASE_PATH || "",

  env: {
    NEXT_PUBLIC_API_URL:      process.env.NEXT_PUBLIC_API_URL      || "http://localhost:8000",
    NEXT_PUBLIC_BOT_USERNAME: process.env.NEXT_PUBLIC_BOT_USERNAME || "gramlycrmtestbot",
    NEXT_PUBLIC_GRAMLY_URL:   process.env.NEXT_PUBLIC_GRAMLY_URL   || "",
  },
};

module.exports = nextConfig;
