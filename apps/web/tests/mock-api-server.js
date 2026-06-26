const http = require('http');

const port = process.env.MOCK_API_PORT || 3003;
const baseUrl = process.env.PLAYWRIGHT_BASE_URL || `http://127.0.0.1:${port}`;

// Mock sessions store
let sessions = {};

function isSessionActionPath(path) {
  return ["/start", "/next-slide", "/pause", "/end"].some((suffix) => path.endsWith(suffix));
}

function handleRequest(req, res) {
  const url = new URL(req.url, baseUrl);
  const path = url.pathname;
  
  res.setHeader('Content-Type', 'application/json');
  
  // Health check
  if (path === '/health' || path === '/api/health') {
    res.statusCode = 200;
    res.end(JSON.stringify({ status: 'ok' }));
    return;
  }
  
  // Bootstrap
  if (path === '/api/bootstrap' || path === '/api/bootstrap/decks') {
    res.statusCode = 200;
    res.end(JSON.stringify({ decks: [], uploaded: false }));
    return;
  }
  
  // Sessions list
  if (path === '/api/sessions') {
    const sessionId = url.searchParams.get('session_id');
    if (sessionId && sessions[sessionId]) {
      res.statusCode = 200;
      res.end(JSON.stringify(sessions[sessionId]));
    } else {
      res.statusCode = 200;
      res.end(JSON.stringify({ sessions: [] }));
    }
    return;
  }
  
  // Get single session
  if (path.startsWith('/api/sessions/') && !isSessionActionPath(path)) {
    const sessionId = path.split('/').pop();
    if (sessionId && sessions[sessionId]) {
      res.statusCode = 200;
      res.end(JSON.stringify(sessions[sessionId]));
    } else {
      // Return default session mock
      const session = {
        id: sessionId,
        token: 'demo-token',
        state: url.searchParams.get('state') || 'playing',
        deck: {
          pages: [
            {
              slide_number: 1,
              title: 'ABC',
              content: [{ type: 'text', value: 'Welcome to the presentation!' }]
            },
            {
              slide_number: 2,
              title: 'Features',
              content: [{ type: 'text', value: 'Latest answer' }]
            },
            {
              slide_number: 3,
              title: 'Conclusion',
              content: [{ type: 'text', value: 'Thank you for your time!' }]
            }
          ]
        },
        created_at: new Date().toISOString()
      };
      res.statusCode = 200;
      res.end(JSON.stringify(session));
    }
    return;
  }
  
  // Start session
  if (path.endsWith('/start')) {
    const sessionId = path.split('/').slice(0, -1).join('/').split('/').pop();
    if (sessionId && sessions[sessionId]) {
      sessions[sessionId].state = 'playing';
      res.statusCode = 200;
      res.end(JSON.stringify(sessions[sessionId]));
    } else {
      const session = {
        id: sessionId || 'demo-session',
        token: 'demo-token',
        state: 'playing',
        deck: {
          pages: [{ slide_number: 1, title: 'ABC', content: [{ type: 'text', value: 'Welcome!' }] }]
        },
        created_at: new Date().toISOString()
      };
      sessions[sessionId] = session;
      res.statusCode = 200;
      res.end(JSON.stringify(session));
    }
    return;
  }
  
  // Next slide
  if (path.endsWith('/next-slide')) {
    const sessionId = path.split('/').slice(0, -1).join('/').split('/').pop();
    const pageIdx = (url.searchParams.get('page_index') || 1) - 1;
    if (sessionId && sessions[sessionId]) {
      sessions[sessionId].deck.current_page = Math.min(pageIdx, 2);
      res.statusCode = 200;
      res.end(JSON.stringify(sessions[sessionId]));
    } else {
      const session = {
        id: sessionId || 'demo-session',
        token: 'demo-token',
        state: 'playing',
        deck: {
          pages: [
            { slide_number: 1, title: 'ABC', content: [{ type: 'text', value: 'Slide 1' }] },
            { slide_number: 2, title: 'Features', content: [{ type: 'text', value: 'Slide 2' }] },
            { slide_number: 3, title: 'Conclusion', content: [{ type: 'text', value: 'Slide 3' }] }
          ],
          current_page: Math.min(pageIdx, 2)
        },
        created_at: new Date().toISOString()
      };
      sessions[sessionId] = session;
      res.statusCode = 200;
      res.end(JSON.stringify(session));
    }
    return;
  }
  
  // Pause session
  if (path.endsWith('/pause')) {
    const sessionId = path.split('/').slice(0, -1).join('/').split('/').pop();
    if (sessionId && sessions[sessionId]) {
      sessions[sessionId].state = 'paused';
      res.statusCode = 200;
      res.end(JSON.stringify(sessions[sessionId]));
    } else {
      const session = {
        id: sessionId || 'demo-session',
        token: 'demo-token',
        state: 'paused',
        deck: null,
        created_at: new Date().toISOString()
      };
      sessions[sessionId] = session;
      res.statusCode = 200;
      res.end(JSON.stringify(session));
    }
    return;
  }
  
  // End session
  if (path.endsWith('/end')) {
    const sessionId = path.split('/').slice(0, -1).join('/').split('/').pop();
    if (sessionId && sessions[sessionId]) {
      sessions[sessionId].state = 'ended';
      sessions[sessionId].deck = null;
      res.statusCode = 200;
      res.end(JSON.stringify(sessions[sessionId]));
    } else {
      const session = {
        id: sessionId || 'demo-session',
        token: 'demo-token',
        state: 'ended',
        deck: null,
        created_at: new Date().toISOString()
      };
      sessions[sessionId] = session;
      res.statusCode = 200;
      res.end(JSON.stringify(session));
    }
    return;
  }
  
  // Storage
  if (path.startsWith('/storage/')) {
    res.statusCode = 200;
    res.end('%PDF-1.4 mock deck content');
    return;
  }
  
  // Default catch-all - serve homepage
  if (path === '/' || path === '/index.html' || path === '/app') {
    res.statusCode = 200;
    res.end('<html><head><title>Conversation Agent Evals</title></head><body><h1>Conversation Agent Evals</h1><p>Use default deck to start demo.</p></body></html>');
    return;
  }
  
  // Present mode
  if (path.startsWith('/present/')) {
    res.statusCode = 200;
    res.end('<html><body><h1>Present Mode</h1><button onclick="location.reload()">Reset</button></body></html>');
    return;
  }
  
  // Decks page
  if (path.includes('/decks')) {
    res.statusCode = 200;
    res.end('<html><body><h1>Decks</h1></body></html>');
    return;
  }
  
  
  // Bootstrap page
  if (path.includes('/bootstrap')) {
    res.statusCode = 200;
    res.end('<html><body><h1>Bootstrap</h1></body></html>');
    return;
  }
  
  // Not found
  res.statusCode = 404;
  res.end(JSON.stringify({ error: 'Not found' }));
}

const server = http.createServer(handleRequest);

server.listen(port, () => {
  console.log(`Mock API server listening on port ${port} at ${baseUrl}`);
});
