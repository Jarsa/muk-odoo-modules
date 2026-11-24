from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import common, tagged
from odoo.sql_db import Savepoint
from odoo.tools import SQL


@tagged('post_install', '-at_install')
class TestToolCommit(common.TransactionCase):
    """Covers tools that commit the transaction themselves while they run."""

    # ----------------------------------------------------------
    # Helper
    # ----------------------------------------------------------

    def _patch_tool(self, body):
        """Run ``body`` as the tool, recording the savepoints ``_call`` opens."""
        savepoints = []
        cursor_class = type(self.env.cr)
        original = cursor_class.savepoint

        def spy(cr, flush=True):
            savepoints.append(original(cr, flush))
            return savepoints[-1]

        def fake(self_arg, name, arguments, env, enforce_scope):
            return body(env, savepoints), {}, None

        return (
            patch.object(cursor_class, 'savepoint', autospec=True, side_effect=spy),
            patch.object(
                type(self.env['muk_mcp.tool']),
                '_execute',
                autospec=True,
                side_effect=fake,
            ),
        )

    def _commit(self, env, savepoints):
        """Act like ``cr.commit()``, which tests forbid: the transaction ends,
        taking the tool's savepoint with it, and the post-commit hooks run.

        A new savepoint marks the start of the next transaction, so that a
        simulated ``cr.rollback()`` only discards what was written after it.
        """
        env.cr.execute(SQL('RELEASE SAVEPOINT %s', SQL.identifier(savepoints[0].name)))
        env.cr.postcommit.run()
        self.transaction_start = Savepoint(env.cr)

    def _rollback(self):
        """Act like ``cr.rollback()`` on the transaction opened by ``_commit``."""
        self.env.cr.clear()
        self.transaction_start.close(rollback=True)
        self.env.cr.postrollback.run()

    def _partner_count(self, name):
        return self.env['res.partner'].search_count([('name', '=', name)])

    # ----------------------------------------------------------
    # Tests
    # ----------------------------------------------------------

    def test_tool_commit_keeps_result(self):
        def body(env, savepoints):
            env['res.partner'].create({'name': 'MCP Commit A'})
            self._commit(env, savepoints)
            env['res.partner'].create({'name': 'MCP Commit B'})
            return 'commit ok'

        spy, tool = self._patch_tool(body)
        with spy, tool:
            text, _info = self.env['muk_mcp.tool']._call('probe', {}, self.env)
        self.assertEqual(text, 'commit ok')
        self.assertEqual(self._partner_count('MCP Commit A'), 1)
        self.assertEqual(self._partner_count('MCP Commit B'), 1)

    def test_tool_commit_keeps_original_error(self):
        def body(env, savepoints):
            env['res.partner'].create({'name': 'MCP Commit A'})
            self._commit(env, savepoints)
            env['res.partner'].create({'name': 'MCP Commit B'})
            raise UserError('Failed after commit')

        spy, tool = self._patch_tool(body)
        with (
            spy,
            tool,
            patch.object(self.env.cr, 'rollback', side_effect=self._rollback) as rollback,
            self.assertRaisesRegex(UserError, 'Failed after commit'),
        ):
            self.env['muk_mcp.tool']._call('probe', {}, self.env)
        rollback.assert_called_once()
        self.assertEqual(self._partner_count('MCP Commit A'), 1)
        self.assertEqual(self._partner_count('MCP Commit B'), 0)
