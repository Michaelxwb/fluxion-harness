import { randomUUID } from 'node:crypto';
import Fastify from 'fastify';

import { logEvent } from './logging.js';
import { failure, ok } from './response.js';

const serviceName = 'channel-gateway';
const app = Fastify({ logger: false });

app.addHook('onRequest', async (request, reply) => {
  const requestId = String(request.headers['x-request-id'] ?? randomUUID().replaceAll('-', ''));
  request.headers['x-request-id'] = requestId;
  reply.header('X-Request-ID', requestId);
});

app.addHook('onResponse', async (request, reply) => {
  logEvent('info', serviceName, 'http_request_completed', {
    request_id: request.headers['x-request-id'],
    method: request.method,
    path: request.url,
    status_code: reply.statusCode,
  });
});

app.setErrorHandler((error, request, reply) => {
  const requestId = String(request.headers['x-request-id'] ?? '');
  logEvent('error', serviceName, 'unhandled_exception', {
    request_id: requestId,
    message: error.message,
  });
  void reply.status(500).send(failure('INTERNAL_ERROR', 'internal server error', requestId));
});

app.get('/health', async (request) => {
  const requestId = String(request.headers['x-request-id'] ?? '');
  return ok({ status: 'ok', service: serviceName }, requestId);
});

// TODO: register selected ChannelAdapters at startup.
// Phase-1 reference: integrations/channels/wecom (not part of Framework Core).

await app.listen({ host: '0.0.0.0', port: Number(process.env.PORT ?? 8010) });
