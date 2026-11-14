import os
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import gmail_app as app


def message(subject='測試主旨', sender='me@example.com', message_id='<original@example.com>', auto=None):
    mail = EmailMessage()
    mail['From'] = sender
    mail['To'] = 'me@example.com'
    mail['Subject'] = subject
    if message_id:
        mail['Message-ID'] = message_id
    if auto:
        mail['Auto-Submitted'] = auto
    mail.set_content('測試內容')
    return mail.as_bytes()


class Inbox:
    def __init__(self, messages):
        self.messages = messages
        self.fetches = []
        self.searches = []
        self.readonly = False
        self.closed = False
    def login(self, address, password): return 'OK', [b'logged in']
    def select(self, mailbox='INBOX', readonly=False):
        self.readonly = readonly
        return 'OK', [str(len(self.messages)).encode()]
    def response(self, name): return 'UIDVALIDITY', [b'42']
    def uid(self, command, *args):
        if command == 'search':
            self.searches.append(args)
            return 'OK', [b' '.join(self.messages)]
        uid, query = args
        self.fetches.append((uid, query))
        return 'OK', [(b'payload', self.messages[uid])]
    def logout(self): self.closed = True


class SMTP:
    def __init__(self): self.messages = []
    def login(self, address, password): pass
    def send_message(self, msg): self.messages.append(msg); return {}
    def quit(self): pass


class ExtraMailActions(unittest.TestCase):
    def test_export_exact_unique_message_is_raw_and_readonly(self):
        raw = message()
        inbox = Inbox({b'1': message('其他'), b'2': raw})
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'saved.eml'
            app.export_message('me@example.com', 'secret', '測試主旨', path, lambda: inbox)
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(inbox.readonly)
        self.assertTrue(inbox.closed)
        self.assertTrue(all('BODY.PEEK' in query for _, query in inbox.fetches))

    def test_export_existing_file_not_overwritten_and_ambiguous_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'saved.eml'
            path.write_text('keep')
            with self.assertRaises(app.AppError):
                app.export_message('me@example.com', 'secret', '測試主旨', path,
                                   lambda: self.fail('existing path must not connect'))
            self.assertEqual(path.read_text(), 'keep')
            with self.assertRaises(app.AppError):
                app.export_message('me@example.com', 'secret', '測試主旨', Path(temp)/'other.eml',
                                   lambda: Inbox({b'1': message(), b'2': message()}))

    def test_auto_reply_only_self_and_once_with_thread_headers(self):
        smtp = SMTP()
        inbox = Inbox({b'3': message()})
        with tempfile.TemporaryDirectory() as temp:
            app.auto_reply_once('me@example.com', 'secret', '測試主旨', '收到', temp,
                                lambda: inbox, lambda: smtp)
            reply = smtp.messages[0]
            self.assertEqual(reply['To'], 'me@example.com')
            self.assertEqual(reply['In-Reply-To'], '<original@example.com>')
            self.assertEqual(reply['Auto-Submitted'], 'auto-replied')
            self.assertEqual(reply['Subject'], 'Re: 測試主旨')
            with self.assertRaises(app.AppError):
                app.auto_reply_once('me@example.com', 'secret', '測試主旨', '收到', temp,
                                    lambda: Inbox({b'3': message()}), lambda: self.fail('duplicate'))
            self.assertEqual(len(smtp.messages), 1)
            self.assertEqual(inbox.searches, [(None, 'UNSEEN')])
            self.assertTrue(inbox.readonly)

    def test_auto_reply_requires_uidvalidity_and_unique_subject(self):
        inbox = Inbox({b'1': message()})
        inbox.response = lambda _: ('UIDVALIDITY', [None])
        for candidate in (inbox, Inbox({b'1': message(), b'2': message()}), Inbox({})):
            with self.subTest(candidate=candidate), tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(app.AppError):
                    app.auto_reply_once('me@example.com', 'secret', '測試主旨', '收到', temp,
                                        lambda: candidate, lambda: self.fail('must not send'))

    def test_cli_accepts_commands_and_saves_redacted_outcomes(self):
        export = app.parser().parse_args(['export', '--subject', 'test', '--out', 'saved.eml', '--save-result'])
        reply = app.parser().parse_args(['auto-reply', '--subject', 'test', '--body', 'ok', '--save-result'])
        self.assertEqual((export.command, export.out), ('export', 'saved.eml'))
        self.assertEqual((reply.command, reply.body), ('auto-reply', 'ok'))
        with tempfile.TemporaryDirectory() as temp:
            for command in ('export', 'auto-reply'):
                path, _ = app.save_result(temp, command, {})
                self.assertNotIn('me@example.com', path.read_text())
                self.assertNotIn('測試主旨', path.read_text())

    def test_auto_reply_rejects_nonself_automatic_and_missing_message_id(self):
        for raw in (message(sender='other@example.com'), message(auto='auto-replied'), message(message_id=None)):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(app.AppError):
                    app.auto_reply_once('me@example.com', 'secret', '測試主旨', '收到', temp,
                                        lambda: Inbox({b'1': raw}), lambda: self.fail('must not send'))

    def test_auto_reply_uncertain_smtp_failure_does_not_retry_same_message(self):
        smtp = SMTP()
        smtp.send_message = lambda _: (_ for _ in ()).throw(OSError('connection lost'))
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(OSError):
                app.auto_reply_once('me@example.com', 'secret', '測試主旨', '收到', temp,
                                    lambda: Inbox({b'1': message()}), lambda: smtp)
            with self.assertRaises(app.AppError):
                app.auto_reply_once('me@example.com', 'secret', '測試主旨', '收到', temp,
                                    lambda: Inbox({b'1': message()}), lambda: self.fail('must not retry'))


if __name__ == '__main__':
    unittest.main()
