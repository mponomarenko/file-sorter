from __future__ import annotations

from .interface import PeekImporter

import textract
import email
import email.policy
from email.message import EmailMessage


class EmailImporter:
    """Extracts text previews from email files (.eml, .msg)."""

    _SUPPORTED_MIMES = {
        'message/rfc822',  # .eml
        'application/vnd.ms-outlook',  # .msg
    }

    def supports(self, path: str, mime: str) -> bool:
        return mime in self._SUPPORTED_MIMES

    def read_preview(self, path: str, limit: int) -> str | None:
        try:
            if path.endswith('.msg'):
                # Use textract for .msg files
                text = textract.process(path).decode('utf-8', errors='ignore')
            else:
                # Use email library for .eml files
                with open(path, 'rb') as f:
                    msg: EmailMessage = email.message_from_binary_file(f, policy=email.policy.default)
                    text = self._extract_text_from_email(msg)
            if not text.strip():
                return None
            return text[:limit]
        except Exception:
            return None

    def _extract_text_from_email(self, msg: EmailMessage) -> str:
        """Extract plain text content from email message."""
        parts = []
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == 'text/plain':
                    charset = part.get_content_charset() or 'utf-8'
                    try:
                        payload = part.get_payload(decode=True)
                        if isinstance(payload, bytes):
                            content = payload.decode(charset, errors='ignore')
                        else:
                            content = str(payload)
                        parts.append(content)
                    except Exception:
                        continue
        else:
            if msg.get_content_type() == 'text/plain':
                charset = msg.get_content_charset() or 'utf-8'
                try:
                    payload = msg.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        content = payload.decode(charset, errors='ignore')
                    else:
                        content = str(payload)
                    parts.append(content)
                except Exception:
                    pass
        # Also include subject
        subject = msg.get('subject', '')
        if subject:
            parts.insert(0, f"Subject: {subject}")
        return '\n'.join(parts)


def build() -> PeekImporter:
    return EmailImporter()


__all__ = ["EmailImporter", "build"]