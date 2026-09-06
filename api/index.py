def handler(request, context):
    return {
        'statusCode': 200,
        'headers': {'Content-Type': 'text/html'},
        'body': '<h1>Common</h1><p>API running.</p>'
    }