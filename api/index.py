def handler(environ, start_response):
    """Pure WSGI handler — Vercel's Python runtime is WSGI-based."""
    path = environ.get('PATH_INFO', '/')
    
    if path == '/api/health':
        start_response('200 OK', [('Content-Type', 'application/json')])
        return [b'{"status": "ok"}']
    
    start_response('200 OK', [('Content-Type', 'text/html')])
    return [b'<h1>Common</h1><p>API running.</p>']