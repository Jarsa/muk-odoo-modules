{
    'name': 'MuK MCP Attachments',
    'summary': 'Upload attachments to any record through the MCP endpoint',
    'description': """
        Adds two MCP tools to attach files to records without corruption:
        upload_attachment for small inline base64 payloads and
        request_attachment_upload, which returns a short-lived signed URL
        the client PUTs the raw bytes to. Size and SHA-256 are verified
        before the attachment is created, with the caller's access rights.
    """,
    'version': '19.0.1.0.0',
    'category': 'Tools/API',
    'license': 'LGPL-3',
    'author': 'MuK IT, Jarsa',
    'website': 'http://www.mukit.at',
    'contributors': [
        'Jesús Alan Ramos Rodríguez <alan.ramos@jarsa.com>',
    ],
    'depends': [
        'muk_mcp',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
