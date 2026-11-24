import json
import secrets

from odoo import fields

from odoo.tests import HttpCase, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestMcpController(HttpCase):
    """Covers authentication and tool calls through the /mcp endpoint."""

    # ----------------------------------------------------------
    # Setup
    # ----------------------------------------------------------

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.token = secrets.token_urlsafe(32)
        key_model = cls.env['muk_mcp.key']
        key_model.sudo().create(
            {
                'name': 'Controller Test Key',
                'user_id': cls.env.ref('base.user_admin').id,
                'key_hash': key_model._hash_key(cls.token),
                'key_prefix': cls.token[:8],
                'scope': 'write',
                'rate_limit': 0,
            },
        )
        tool_model = cls.env['muk_mcp.tool'].sudo()
        for name, code in (
            (
                'test_rollback_ok',
                "env['res.partner'].create({'name': 'MCP Rollback OK'})\nresult = 'done'",
            ),
            (
                'test_rollback_user_error',
                "env['res.partner'].create({'name': 'MCP Rollback UserError'})\n"
                "raise UserError('Rollback user error')",
            ),
            (
                'test_rollback_exception',
                "env['res.partner'].create({'name': 'MCP Rollback Exception'})\n"
                "result = 1 / 0",
            ),
            (
                'test_rollback_flush_error',
                "partner = env['res.partner'].create({'name': 'MCP Rollback Flush'})\n"
                "partner.write({'name': False})",
            ),
            (
                # The sequence survives the rollback, so only the first try fails.
                'test_retry_serialization',
                "env['res.partner'].create({'name': 'MCP Retry'})\n"
                "env.cr.execute(\"SELECT nextval('test_muk_mcp_retry_seq')\")\n"
                "attempt = env.cr.fetchone()[0]\n"
                "if attempt == 1:\n"
                "    env.cr.execute(\"DO $$ BEGIN RAISE EXCEPTION 'retry me' "
                "USING ERRCODE = '40001'; END $$\")\n"
                "result = attempt",
            ),
            (
                'test_rollback_integrity_error',
                "env['res.partner'].create({'name': 'MCP Rollback Integrity'})\n"
                "env['res.currency'].create({'name': 'USD', 'symbol': 'X'})",
            ),
        ):
            tool_model.create(
                {
                    'name': name,
                    'category': 'write',
                    'description': name,
                    'code': code,
                },
            )

        cls.env.cr.execute('CREATE SEQUENCE test_muk_mcp_retry_seq')
        cls.env['muk_mcp.prompt'].sudo().create(
            {
                'name': 'test_rollback_prompt',
                'title': 'Rollback prompt',
                'description': 'Creates a partner, then fails.',
                'body': "env['res.partner'].create({'name': 'MCP Rollback Prompt'})\n"
                "raise UserError('Rollback prompt error')",
            },
        )

    # ----------------------------------------------------------
    # Helper
    # ----------------------------------------------------------

    def _mcp_request(self, data, headers=None):
        all_headers = {
            'Content-Type': 'application/json',
        }
        if headers:
            all_headers.update(headers)
        return self.url_open(
            '/mcp',
            data=json.dumps(data),
            headers=all_headers,
        )

    def _mcp_message(self, method, params=None, request_id=1):
        data = {'jsonrpc': '2.0', 'method': method, 'params': params or {}}
        if request_id is not None:
            data['id'] = request_id
        return data

    def _mcp_send(self, data, session_id=None):
        headers = {'Authorization': f'Bearer {self.token}'}
        if session_id:
            headers['Mcp-Session-Id'] = session_id
        return self._mcp_request(data, headers=headers)

    def _mcp_call(self, method, params=None, session_id=None, request_id=1):
        return self._mcp_send(
            self._mcp_message(method, params, request_id),
            session_id=session_id,
        )

    def _open_session(self):
        response = self._mcp_call('initialize')
        self.assertEqual(response.status_code, 200)
        session_id = response.headers['Mcp-Session-Id']
        self._mcp_call(
            'notifications/initialized',
            session_id=session_id,
            request_id=None,
        )
        return session_id

    def _call_tool(self, name, session_id=None):
        response = self._mcp_call(
            'tools/call',
            {'name': name, 'arguments': {}},
            session_id=session_id or self._open_session(),
        )
        self.assertEqual(response.status_code, 200)
        return response.json()['result']

    def _tool_message(self, name, request_id):
        return self._mcp_message(
            'tools/call',
            {'name': name, 'arguments': {}},
            request_id,
        )

    def _partner_count(self, name):
        return (
            self.env['res.partner']
            .with_context(active_test=False)
            .search_count([('name', '=', name)])
        )

    def _partner_exists(self, name):
        return bool(self._partner_count(name))

    # ----------------------------------------------------------
    # Tests
    # ----------------------------------------------------------

    def test_mcp_post_without_auth_is_rejected(self):
        response = self._mcp_request(
            {
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'initialize',
                'params': {},
            },
        )
        self.assertNotEqual(response.status_code, 200)

    def test_mcp_get_without_auth_is_rejected(self):
        response = self.url_open(
            '/mcp',
            headers={
                'Accept': 'text/event-stream',
            },
        )
        self.assertNotEqual(response.status_code, 200)

    def test_tool_call_success_keeps_writes(self):
        result = self._call_tool('test_rollback_ok')
        self.assertFalse(result.get('isError'))
        self.assertTrue(self._partner_exists('MCP Rollback OK'))

    def test_tool_call_user_error_rolls_back(self):
        result = self._call_tool('test_rollback_user_error')
        self.assertTrue(result['isError'])
        self.assertEqual(result['content'][0]['text'], 'Rollback user error')
        self.assertFalse(self._partner_exists('MCP Rollback UserError'))

    def test_tool_call_exception_rolls_back(self):
        result = self._call_tool('test_rollback_exception')
        self.assertTrue(result['isError'])
        self.assertIn('Internal server error', result['content'][0]['text'])
        self.assertIn('division by zero', result['content'][0]['text'])
        self.assertFalse(self._partner_exists('MCP Rollback Exception'))

    @mute_logger('odoo.sql_db')
    def test_tool_call_flush_error_rolls_back(self):
        result = self._call_tool('test_rollback_flush_error')
        self.assertTrue(result['isError'])
        self.assertIn('The operation cannot be completed', result['content'][0]['text'])
        self.assertFalse(self._partner_exists('MCP Rollback Flush'))

    @mute_logger('odoo.sql_db')
    def test_tool_call_integrity_error_rolls_back(self):
        result = self._call_tool('test_rollback_integrity_error')
        self.assertTrue(result['isError'])
        self.assertIn('The operation cannot be completed', result['content'][0]['text'])
        self.assertFalse(self._partner_exists('MCP Rollback Integrity'))

    def test_tool_call_failure_keeps_session_and_log(self):
        session_id = self._open_session()
        result = self._call_tool('test_rollback_user_error', session_id=session_id)
        self.assertTrue(result['isError'])
        session = self.env['muk_mcp.session'].sudo().search(
            [('session_id', '=', session_id)],
        )
        self.assertTrue(session.active)
        self.assertTrue(session.initialized)
        log = self.env['muk_mcp.log'].sudo().search(
            [('tool_name', '=', 'test_rollback_user_error')],
        )
        self.assertEqual(len(log), 1)
        self.assertEqual(log.status, 'error')
        self.assertEqual(log.error_message, 'Rollback user error')
        self.assertEqual(log.key_name, 'Controller Test Key')

    def test_batch_failed_tool_call_keeps_other_calls(self):
        response = self._mcp_send(
            [
                self._tool_message('test_rollback_ok', 1),
                self._tool_message('test_rollback_user_error', 2),
                self._tool_message('test_rollback_ok', 3),
            ],
            session_id=self._open_session(),
        )
        self.assertEqual(response.status_code, 200)
        results = {item['id']: item['result'] for item in response.json()}
        self.assertFalse(results[1].get('isError'))
        self.assertTrue(results[2]['isError'])
        self.assertFalse(results[3].get('isError'))
        self.assertEqual(self._partner_count('MCP Rollback OK'), 2)
        self.assertFalse(self._partner_exists('MCP Rollback UserError'))

    @mute_logger('odoo.sql_db')
    def test_batch_integrity_error_keeps_new_session(self):
        session_model = self.env['muk_mcp.session'].sudo()
        session_id = self._open_session()
        count = session_model.search_count([])
        response = self._mcp_send(
            [
                self._mcp_message('initialize', request_id=1),
                self._tool_message('test_rollback_integrity_error', 2),
            ],
            session_id=session_id,
        )
        self.assertEqual(response.status_code, 200)
        results = {item['id']: item['result'] for item in response.json()}
        self.assertTrue(results[2]['isError'])
        self.assertEqual(session_model.search_count([]), count + 1)
        self.assertFalse(self._partner_exists('MCP Rollback Integrity'))

    def test_prompt_get_error_rolls_back(self):
        response = self._mcp_call(
            'prompts/get',
            {'name': 'test_rollback_prompt', 'arguments': {}},
            session_id=self._open_session(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('Rollback prompt error', response.json()['error']['message'])
        self.assertFalse(self._partner_exists('MCP Rollback Prompt'))

    @mute_logger('odoo.sql_db', 'odoo.service.model')
    def test_tool_call_serialization_failure_is_retried(self):
        result = self._call_tool('test_retry_serialization')
        self.assertFalse(result.get('isError'))
        self.assertEqual(result['content'][0]['text'], '2')
        self.assertEqual(self._partner_count('MCP Retry'), 1)

    @mute_logger('odoo.sql_db')
    def test_integrity_error_keeps_session_activity(self):
        old_time = fields.Datetime.subtract(fields.Datetime.now(), hours=1)
        session = (
            self.env['muk_mcp.session']
            .sudo()
            .create(
                {
                    'user_id': self.env.ref('base.user_admin').id,
                    'initialized': True,
                    'last_activity': old_time,
                },
            )
        )
        result = self._call_tool(
            'test_rollback_integrity_error',
            session_id=session.session_id,
        )
        self.assertTrue(result['isError'])
        session.invalidate_recordset()
        self.assertGreater(session.last_activity, old_time)
