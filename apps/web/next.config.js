/** @type {import('next').NextConfig} */
// Rewrites run server-side inside Next. In Docker, prefer internal service URLs
// (`API_BASE_URL=http://api:8000`) over browser-facing NEXT_PUBLIC localhost URLs.
const apiBase = (process.env.API_BASE_URL || process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8025').replace(/\/$/, '');
const pipecatBase = (process.env.PIPECAT_SERVICE_URL || process.env.NEXT_PUBLIC_PIPECAT_SERVICE_URL || 'http://localhost:8110').replace(/\/$/, '');
const productionFlag = process.env.PRODUCTION === 'true' || process.env.NEXT_PUBLIC_PRODUCTION === 'true' || process.env.APP_ENV === 'production';

const nextConfig = {
  typedRoutes: true,
  env: {
    NEXT_PUBLIC_PRODUCTION: String(productionFlag),
  },
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${apiBase}/api/:path*`,
      },
      {
        source: '/pipecat/:path*',
        destination: `${pipecatBase}/:path*`,
      },
      {
        source: '/storage/:path*',
        destination: `${apiBase}/storage/:path*`,
      },
    ];
  },
  webpack: (config, { dev, isServer }) => {
    // Robust local-demo fix: stale standalone/runtime artifacts can leave server bundles
    // requiring vendor-chunks that do not exist in a normal next build/start flow.
    // Disable the separate vendor chunk split on server prod builds so the route bundle
    // stays self-contained across local rebuilds.
    if (!dev && isServer && config.optimization?.splitChunks) {
      config.optimization.splitChunks = false;
    }
    return config;
  },
};

module.exports = nextConfig;
