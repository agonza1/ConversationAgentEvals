import http from 'http';
import type { Server } from 'http';
import type { Request, Response } from 'express';

const mockResponses = {
  '/api/health': { status: 'ok' },
  
  // Sessions API
  '/api/sessions': {
    method: 'get',
    response: async (req: Request, res: Response) => {
      const sessions: any[] = [];
      if (req.query.session_id) {
        sessions.push({
          id: req.query.session_id as string,
          token: 'demo-token',
          state: 'idle',
          deck: null,
          created_at: new Date().toISOString(),
        });
      }
      res.setHeader('Content-Type', 'application/json');
      res.statusCode = 200;
      res.end(JSON.stringify(sessions));
    },
  },
  '/api/sessions/:session_id': {
    method: 'get',
    response: async (req: Request, res: Response) => {
      const sessionId = (req.params.session_id as string) || 'demo-session';
      const session: any = {
        id: sessionId,
        token: 'demo-token',
        state: 'idle',
        deck: {
          pages: [
            {
              slide_number: 1,
              title: 'ABC',
              content: [{ type: 'text', value: 'Welcome to the presentation!' }],
            },
            {
              slide_number: 2,
              title: 'Features',
              content: [{ type: 'text', value: 'Latest answer' }],
            },
            {
              slide_number: 3,
              title: 'Conclusion',
              content: [{ type: 'text', value: 'Thank you for your time!' }],
            },
          ],
        },
        created_at: new Date().toISOString(),
      };
      res.setHeader('Content-Type', 'application/json');
      res.statusCode = 200;
      res.end(JSON.stringify(session));
    },
  },
  '/api/sessions/:session_id/start': {
    method: 'post',
    response: async (req: Request, res: Response) => {
      const sessionId = (req.params.session_id as string) || 'demo-session';
      const session: any = {
        id: sessionId,
        token: 'demo-token',
        state: 'playing',
        deck: {
          pages: [
            {
              slide_number: 1,
              title: 'ABC',
              content: [{ type: 'text', value: 'Welcome to the presentation!' }],
            },
          ],
        },
        created_at: new Date().toISOString(),
      };
      res.setHeader('Content-Type', 'application/json');
      res.statusCode = 200;
      res.end(JSON.stringify(session));
    },
  },
  '/api/sessions/:session_id/next-slide': {
    method: 'post',
    response: async (req: Request, res: Response) => {
      const sessionId = (req.params.session_id as string) || 'demo-session';
      const pageIdx = Math.min((req.body.page_index ?? 1) - 1, 2);
      const session: any = {
        id: sessionId,
        token: 'demo-token',
        state: 'playing',
        deck: {
          pages: [
            { slide_number: 1, title: 'ABC', content: [{ type: 'text', value: 'Slide 1' }] },
            { slide_number: 2, title: 'Features', content: [{ type: 'text', value: 'Slide 2' }] },
            { slide_number: 3, title: 'Conclusion', content: [{ type: 'text', value: 'Slide 3' }] },
          ],
        },
        created_at: new Date().toISOString(),
      };
      // Update current slide
      session.deck!.current_page = pageIdx;
      res.setHeader('Content-Type', 'application/json');
      res.statusCode = 200;
      res.end(JSON.stringify(session));
    },
  },
  '/api/sessions/:session_id/pause': {
    method: 'post',
    response: async (req: Request, res: Response) => {
      const sessionId = (req.params.session_id as string) || 'demo-session';
      const session: any = {
        id: sessionId,
        token: 'demo-token',
        state: 'paused',
        deck: null,
        created_at: new Date().toISOString(),
      };
      res.setHeader('Content-Type', 'application/json');
      res.statusCode = 200;
      res.end(JSON.stringify(session));
    },
  },
  '/api/sessions/:session_id/end': {
    method: 'post',
    response: async (req: Request, res: Response) => {
      const sessionId = (req.params.session_id as string) || 'demo-session';
      const session: any = {
        id: sessionId,
        token: 'demo-token',
        state: 'ended',
        deck: null,
        created_at: new Date().toISOString(),
      };
      res.setHeader('Content-Type', 'application/json');
      res.statusCode = 200;
      res.end(JSON.stringify(session));
    },
  },
};

function createMockServer(): Server {
  const server = http.createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json');
    
    // Get the mock response for this route
    const route = req.url?.split('?')[0] || '/';
    const mockRoute = `/api${route}`;
    
    if (mockResponses[mockRoute]) {
      const handler = mockResponses[mockRoute];
      if (handler.method === 'get') {
        res.statusCode = 200;
        res.end(JSON.stringify({ status: 'ok' }));
      } else if (handler.method === 'post') {
        res.statusCode = 200;
        res.end(JSON.stringify({ status: 'ok' }));
      } else {
        res.statusCode = 404;
        res.end(JSON.stringify({ error: 'Not found' }));
      }
    } else if (route === '/health') {
      res.statusCode = 200;
      res.end(JSON.stringify({ status: 'ok' }));
    } else if (route === '/storage/test.pdf') {
      // Return a mock PDF
      res.statusCode = 200;
      res.end('%PDF-1.4 mock pdf content');
    } else if (route === '/storage/default.pdf') {
      res.statusCode = 200;
      res.end('%PDF-1.4 default deck mock');
    } else {
      // Serve static files for web pages
      if (route.startsWith('/')) {
        const path = `/${route.replace(/[^/]/g, 'x')}`;
        // Serve placeholder for web pages
        if (route.includes('/benchmarks') || route === '/') {
          res.statusCode = 200;
          res.end('<html><body><h1>Conversation Agent Evals</h1><p>Use default deck to start demo.</p></body></html>');
        } else if (route.includes('/present/')) {
          res.statusCode = 200;
          res.end('<html><body><h1>Present Mode</h1><button onclick="location.reload()">Reset</button></body></html>');
        } else if (route.includes('/decks')) {
          res.statusCode = 200;
          res.end('<html><body><h1>Decks</h1></body></html>');
        } else if (route.includes('/bootstrap')) {
          res.statusCode = 200;
          res.end('<html><body><h1>Bootstrap</h1></body></html>');
        } else {
          res.statusCode = 200;
          res.end('<html><body><h1>Conversation Agent Evals</h1></body></html>');
        }
      } else {
        res.statusCode = 404;
        res.end(JSON.stringify({ error: 'Not found' }));
      }
    }
  });
  
  return server;
}

export default createMockServer;
