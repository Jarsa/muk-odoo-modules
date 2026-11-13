# Copyright 2026, Jarsa
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

import base64
import hashlib
import os

from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestMCPAttachment(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'MCP Upload'})
        # Many '&' and '=' like a real PDF: form parsing would explode them
        # into thousands of fields and abort with 413.
        cls.raw = os.urandom(4096) + b'a=1&' * 2000 + b'\x00\xff\r\n'
        cls.sha = hashlib.sha256(cls.raw).hexdigest()
        cls.admin = cls.env.ref('base.user_admin')
        cls.mixin = cls.env['muk_mcp.mixin'].with_user(cls.admin)

    def _request(self, **kw):
        values = {
            'model': 'res.partner',
            'res_id': self.partner.id,
            'name': 'blob.bin',
            'size': len(self.raw),
            'sha256': self.sha,
            **kw,
        }
        return self.mixin._mcp_request_attachment_upload(**values)['upload_url']

    def test_inline_upload_roundtrip(self):
        res = self.mixin._mcp_upload_attachment(
            'res.partner',
            self.partner.id,
            'blob.bin',
            base64.b64encode(self.raw).decode(),
            sha256=self.sha,
            size=len(self.raw),
        )
        attachment = self.env['ir.attachment'].browse(res['id'])
        self.assertEqual(attachment.raw, self.raw)
        self.assertEqual(attachment.res_id, self.partner.id)
        # Retrying the same upload must not duplicate it.
        again = self.mixin._mcp_upload_attachment(
            'res.partner', self.partner.id, 'blob.bin', base64.b64encode(self.raw).decode()
        )
        self.assertEqual(again['id'], res['id'])

    def test_inline_rejects_corruption(self):
        corrupted = base64.b64encode(self.raw[:-1] + b'x').decode()
        with self.assertRaises(ValidationError):
            self.mixin._mcp_upload_attachment(
                'res.partner', self.partner.id, 'blob.bin', corrupted, sha256=self.sha
            )
        with self.assertRaises(ValidationError):
            self.mixin._mcp_upload_attachment(
                'res.partner', self.partner.id, 'blob.bin', 'not base64!!'
            )

    def test_requires_write_access(self):
        portal = new_test_user(self.env, 'mcp_portal', groups='base.group_portal')
        with self.assertRaises(AccessError):
            self.mixin.with_user(portal)._mcp_request_attachment_upload(
                'res.partner', self.partner.id, 'blob.bin', len(self.raw), self.sha
            )

    def test_signed_url_upload(self):
        url = self._request()
        response = self.opener.put(
            url, data=self.raw, headers={'Content-Type': 'application/octet-stream'}
        )
        self.assertEqual(response.status_code, 200, response.text)
        attachment = self.env['ir.attachment'].browse(response.json()['id'])
        self.assertEqual(attachment.raw, self.raw)
        self.assertEqual(attachment.create_uid, self.admin)

    def test_signed_url_rejects_bad_payloads(self):
        url = self._request()
        self.assertEqual(self.opener.put(url, data=self.raw[:-1]).status_code, 400)
        self.assertEqual(self.opener.put(url, data=self.raw + b'!').status_code, 400)
        tampered = url[:-3] + ('AAA' if not url.endswith('AAA') else 'BBB')
        self.assertEqual(self.opener.put(tampered, data=self.raw).status_code, 403)
        self.assertFalse(
            self.env['ir.attachment'].search(
                [('res_model', '=', 'res.partner'), ('res_id', '=', self.partner.id)]
            )
        )
