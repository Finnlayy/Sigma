/** Compatibility entrypoint: the real FastAPI API is proxied by vite.config.ts.
 * No mock API server and no second independent Kraken paper ledger.
 */
import { createServer } from 'vite';

const server = await createServer();
await server.listen();
server.printUrls();
