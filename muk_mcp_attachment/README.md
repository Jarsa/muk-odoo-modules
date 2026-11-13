# MuK MCP Attachments

Adds two MCP tools to attach files to any record without corruption.

| Tool | Use |
|------|-----|
| `request_attachment_upload` | **Preferred.** Takes `model`, `res_id`, `name`, `size`, `sha256`; returns a signed `upload_url` valid 15 minutes. Send the bytes with `curl -sS -X PUT --data-binary @file '<upload_url>'`. |
| `upload_attachment` | Small files only: `content_base64` (+ optional `sha256`/`size`). |

Both verify size and SHA-256 before creating the `ir.attachment`, run with the
caller's access rights (write access on the target record is required, plus
`muk_mcp_access` model/record rules when installed), and are idempotent: the
same bytes and name on the same record return the existing attachment.

The upload URL is an HMAC-signed token (`hash_sign`, `database.secret`) bound to
the user, record, file name, size and checksum, so it cannot be reused for a
different file or record.
