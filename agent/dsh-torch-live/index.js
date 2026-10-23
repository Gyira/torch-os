/**
 * torch-live-stream — a read-only DSH host plugin.
 *
 * DeepSeek Harness's ACP surface emits assistant text/reasoning only after a
 * step commits. The Web UI gets the real "typing" experience from the live
 * `agent/assistant-stream` event. This plugin taps that event and republishes
 * it on a loopback-only SSE endpoint for the torch workbench.
 *
 * It never drives the agent, never mutates a session, and never writes stdout.
 */
import http from 'node:http';

export const name = 'torch-live-stream';

const DEFAULT_PORT = 9877;
const HEARTBEAT_MS = 15000;
const MAX_ARGUMENTS_PREVIEW = 4000;

function jsonLine(payload) {
  return `data: ${JSON.stringify(payload)}\n\n`;
}

function chunkPayload(sessionId, chunk) {
  if (!chunk) return null;
  switch (chunk.type) {
    case 'block-start':
      return { type: 'block_start', sessionId, blockType: chunk.blockType };
    case 'block-end':
      return { type: 'block_end', sessionId, blockType: chunk.block?.type };
    case 'text-delta':
      return chunk.text ? { type: 'text', sessionId, text: String(chunk.text) } : null;
    case 'reasoning-delta':
      return chunk.text ? { type: 'thought', sessionId, text: String(chunk.text) } : null;
    case 'tool-call-delta':
      return {
        type: 'tool_delta',
        sessionId,
        id: String(chunk.id ?? ''),
        name: chunk.name,
        arguments: String(chunk.argumentsDelta ?? '').slice(0, MAX_ARGUMENTS_PREVIEW),
      };
    case 'usage':
      return { type: 'usage', sessionId, usage: chunk.usage };
    case 'finish':
      return { type: 'finish', sessionId, reason: chunk.reason };
    default:
      return null;
  }
}

export function apply(ctx) {
  const port = Number(process.env.TORCH_LIVE_PORT || DEFAULT_PORT);
  const token = String(process.env.TORCH_LIVE_TOKEN || '');
  const allowAll = process.env.TORCH_LIVE_ALLOW_ALL === '1';
  const clients = new Set();

  const server = http.createServer((request, response) => {
    let url;
    try {
      url = new URL(request.url || '/', 'http://127.0.0.1');
    } catch {
      response.writeHead(400); response.end(); return;
    }

    if (url.pathname === '/health') {
      response.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
      response.end(JSON.stringify({ ok: true, clients: clients.size, port }));
      return;
    }

    if (url.pathname !== '/events') {
      response.writeHead(404); response.end(); return;
    }
    if (token && url.searchParams.get('token') !== token) {
      response.writeHead(401); response.end('unauthorized'); return;
    }
    const sessionId = String(url.searchParams.get('session') || '');
    if (!sessionId && !allowAll) {
      response.writeHead(400); response.end('session is required'); return;
    }

    response.writeHead(200, {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    });
    response.write(': connected\n\n');
    const client = { response, sessionId };
    clients.add(client);
    request.on('close', () => clients.delete(client));
  });

  const send = (payload) => {
    if (!payload) return;
    const line = jsonLine(payload);
    for (const client of clients) {
      if (client.sessionId && String(payload.sessionId) !== client.sessionId) continue;
      try { client.response.write(line); } catch { clients.delete(client); }
    }
  };

  ctx.effect(() => {
    const heartbeat = setInterval(() => {
      for (const client of clients) {
        try { client.response.write(': ping\n\n'); } catch { clients.delete(client); }
      }
    }, HEARTBEAT_MS);
    if (heartbeat.unref) heartbeat.unref();

    const onError = (error) => {
      process.stderr.write(`[torch-live] listen failed on 127.0.0.1:${port}: ${error?.message || error}\n`);
    };
    server.on('error', onError);
    server.listen(port, '127.0.0.1', () => {
      process.stderr.write(`[torch-live] listening on 127.0.0.1:${port}\n`);
    });

    return () => {
      clearInterval(heartbeat);
      for (const client of clients) {
        try { client.response.end(); } catch { /* already closed */ }
      }
      clients.clear();
      try { server.close(); } catch { /* not listening */ }
    };
  });

  ctx.effect(() => ctx.on('agent/assistant-stream', ({ agent, frame }) => {
    try {
      const sessionId = String(agent?.session?.id ?? agent?.id ?? '');
      if (!sessionId || !frame) return;
      if (frame.type === 'start') {
        send({ type: 'stream_start', sessionId, turn: frame.turn, step: frame.step });
        return;
      }
      if (frame.type === 'end') {
        send({ type: 'stream_end', sessionId, outcome: frame.outcome });
        return;
      }
      if (frame.type === 'chunk') {
        send(chunkPayload(sessionId, frame.chunk));
      }
    } catch (error) {
      process.stderr.write(`[torch-live] event handling failed: ${error?.message || error}\n`);
    }
  }));
}
