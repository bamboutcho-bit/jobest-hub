/**
 * Standalone Playwright Browser Server.
 * Exposes a predictable WebSocket endpoint compatible with Python Playwright's connect().
 * Defaults to Firefox because Chromium segfaults on Ubuntu 24.04 (Noble) within WSL2 Docker.
 */
const { firefox, chromium } = require('playwright');
const http = require('http');

async function start() {
  const port = parseInt(process.env.PORT || '3000', 10);
  const host = process.env.HOST || '0.0.0.0';
  const wsPath = process.env.WS_PATH || 'browser';
  const browserType = process.env.BROWSER_TYPE || 'firefox';

  console.log(`[BrowserServer] Starting ${browserType} on ${host}:${port}/${wsPath}...`);

  const launcher = browserType === 'chromium' ? chromium : firefox;
  const launchOptions = {
    port,
    host,
    wsPath,
    headless: true,
  };

  if (browserType === 'chromium') {
    launchOptions.args = [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
    ];
  }

  let server;
  try {
    server = await launcher.launchServer(launchOptions);
    console.log(`[BrowserServer] Ready! WebSocket endpoint: ${server.wsEndpoint()}`);
  } catch (err) {
    if (browserType === 'chromium') {
      console.warn(`[BrowserServer] Chromium failed to launch (${err.message}), falling back to Firefox...`);
      server = await firefox.launchServer({ port, host, wsPath, headless: true });
      console.log(`[BrowserServer] Ready (Firefox fallback)! WebSocket endpoint: ${server.wsEndpoint()}`);
    } else {
      throw err;
    }
  }

  // Simple HTTP healthcheck responder on /healthz or GET /
  const healthPort = port + 1;
  const healthServer = http.createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/plain' });
    res.end('OK');
  });
  healthServer.listen(healthPort, host, () => {
    console.log(`[BrowserServer] Healthcheck listening on http://${host}:${healthPort}/`);
  });

  // Graceful shutdown
  const shutdown = async () => {
    console.log('[BrowserServer] Shutting down...');
    if (healthServer) healthServer.close();
    if (server) await server.close();
    process.exit(0);
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}

start().catch(err => {
  console.error('[BrowserServer] Fatal error:', err);
  process.exit(1);
});
