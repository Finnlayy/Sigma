import express from 'express';
import cors from 'cors';
import { createServer as createViteServer } from 'vite';
import path from 'path';
import { krakenRouter } from './src/server/kraken/routes';

async function startServer() {
  const app = express();
  app.disable('x-powered-by'); // Security: Do not leak Express version
  const PORT = 3000;

  app.use(cors());
  app.use(express.json());

  // K-1 — Kraken-Layer (public data only, paper only, fail-closed).
  // Muss VOR dem Mock-Catch-all gemountet werden.
  app.use('/api/kraken', krakenRouter());

  // Mock API routes
  app.get('/api/v1/health', (req, res) => {
    res.json({ status: 'ok', uptime: 1000, blueprint: {} });
  });

  app.get('/api/dashboard/init', (req, res) => {
    res.json({
      status: 'ok',
      uptime: 1000,
      strategies: [],
      system_state: 'IDLE',
      alerts: []
    });
  });

  app.get('/api/strategies', (req, res) => {
    res.json([]);
  });

  app.get('/api/v1/blueprint', (req, res) => {
    res.json({ spec: {}, loops: {}, api_contract: {}, delivery_phases: {}, config: {} });
  });

  // Catch-all for other /api routes to prevent 404 errors hanging the frontend
  app.all('/api/*all', (req, res) => {
    res.json({
      status: 'mocked',
      ok: true,
      available: false,
      message: 'This endpoint is mocked for the AI Studio preview environment.',
      data: []
    });
  });

  // Vite middleware for development
  if (process.env.NODE_ENV !== 'production') {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), 'dist');
    app.use(express.static(distPath));
    app.get('*all', (req, res) => {
      res.sendFile(path.join(distPath, 'index.html'));
    });
  }

  app.listen(PORT, "0.0.0.0", () => {
    console.log(`Server running on http://localhost:${PORT}`);
  });
}

startServer();
