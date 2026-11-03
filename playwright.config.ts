import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests/desktop', testMatch: '*.spec.ts', workers: 1,
  timeout: 240_000, expect: { timeout: 30_000 },
  reporter: process.env.CI ? [['list'], ['github']] : [['list']], use: { trace: 'retain-on-failure' },
});
