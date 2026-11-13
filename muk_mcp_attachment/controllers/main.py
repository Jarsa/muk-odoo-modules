# Copyright 2026, Jarsa
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from odoo import api, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request
from odoo.tools.misc import verify_hash_signed

from ..mcp.attachment import UPLOAD_SCOPE


class MCPAttachmentController(http.Controller):
    @http.route(
        '/mcp/attachment/upload/<string:token>',
        type='http',
        auth='none',
        methods=['PUT', 'POST'],
        csrf=False,
        save_session=False,
        readonly=False,
    )
    def mcp_attachment_upload(self, token, **kwargs):
        try:
            payload = verify_hash_signed(request.env(su=True), UPLOAD_SCOPE, token)
        except ValueError:
            payload = None
        if not payload:
            return request.make_json_response(
                {'error': 'Invalid or expired upload URL.'}, status=403
            )
        user = request.env['res.users'].sudo().browse(payload['uid']).exists()
        if not user.active:
            return request.make_json_response({'error': 'User inactive.'}, status=403)
        # Refuse an oversized body before buffering it; chunked bodies without
        # a length are still capped by Odoo's max_content_length.
        if (request.httprequest.content_length or 0) > payload['size']:
            return request.make_json_response(
                {'error': 'Body larger than the declared size.'}, status=400
            )
        raw = request.httprequest.get_data()
        env = api.Environment(request.env.cr, user.id, {})
        mixin = env['muk_mcp.mixin']
        try:
            mixin._mcp_attachment_verify(raw, payload['size'], payload['sha256'])
            result = mixin._mcp_attachment_create(
                payload['model'],
                payload['res_id'],
                payload['name'],
                raw,
                payload['mimetype'] or None,
            )
        except (AccessError, UserError, ValidationError) as exc:
            request.env.cr.rollback()
            return request.make_json_response({'error': str(exc)}, status=400)
        return request.make_json_response(result)
