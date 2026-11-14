import io
import os
import sys
import unittest
from email.message import EmailMessage
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import gmail_app as app


class FakeIMAP:
    def __init__(self, messages=None, search_results=None):
        self.messages = messages or {}
        self.search_results = list(search_results or [])
        self.closed = False
        self.searches = []
    def login(self, address, password):
        return 'OK', [b'logged in']
    def select(self, mailbox='INBOX', readonly=False):
        return 'OK', [b'0']
    def uid(self, command, *args):
        if command == 'search':
            self.searches.append(args)
            return 'OK', [self.search_results.pop(0) if self.search_results else b'']
        return 'OK', [(b'header', self.messages[args[0]])]
    def logout(self):
        self.closed = True


class FakeSMTP:
    def __init__(self):
        self.messages = []
        self.closed = False
    def login(self, address, password):
        pass
    def send_message(self, message):
        self.messages.append(message)
    def quit(self):
        self.closed = True


class GmailTests(unittest.TestCase):
    def test_settings_uses_prompt_without_password_environment(self):
        with patch.dict(os.environ, {'GMAIL_ADDRESS': ' me@example.com '}, clear=True):
            self.assertEqual(app.load_settings(lambda _: 'secret'), ('me@example.com', 'secret'))

    def test_settings_reads_app_password_from_environment_without_prompt(self):
        with patch.dict(os.environ, {'GMAIL_ADDRESS': 'me@example.com', 'GMAIL_APP_PASSWORD': 'abcd efgh'}, clear=True):
            self.assertEqual(app.load_settings(lambda _: self.fail('unexpected prompt')), ('me@example.com', 'abcdefgh'))

    def test_settings_rejects_missing_address_and_empty_password(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(app.AppError):
            app.load_settings(lambda _: 'secret')
        with patch.dict(os.environ, {'GMAIL_ADDRESS': 'me@example.com', 'GMAIL_APP_PASSWORD': ''}, clear=True), self.assertRaises(app.AppError):
            app.load_settings(lambda _: '')

    def test_non_ascii_password_is_rejected_without_connection_or_disclosure(self):
        commands = (['list'], ['send', '--to', 'me@example.com', '--subject', 'x', '--body', 'y'],
                    ['verify', '--subject', 'x', '--body', 'y'])
        for command in commands:
            with (self.subTest(command=command[0]),
                  patch.dict(os.environ, {'GMAIL_ADDRESS': 'me@example.com', 'GMAIL_APP_PASSWORD': '假密碼'}, clear=True),
                  patch.object(app, 'list_latest') as listing,
                  patch.object(app, 'send_mail') as sending,
                  patch.object(app, 'verify_delivery') as verifying,
                  patch('sys.stderr', new_callable=io.StringIO) as err):
                self.assertEqual(app.main(command), 1)
                self.assertIn('應用程式密碼', err.getvalue())
                self.assertNotIn('假密碼', err.getvalue())
                self.assertNotIn('Traceback', err.getvalue())
                listing.assert_not_called()
                sending.assert_not_called()
                verifying.assert_not_called()

    def test_list_latest_decodes_headers_newest_first_and_closes(self):
        def mail(subject):
            msg = EmailMessage()
            msg['From'] = 'sender@example.com'
            msg['Date'] = 'Fri, 01 Jan 2021 00:00:00 +0000'
            msg['Subject'] = subject
            return msg.as_bytes()
        imap = FakeIMAP({b'2': mail('第二封'), b'3': mail('第三封')}, [b'1 2 3'])
        rows = app.list_latest('me@example.com', 'secret', 2, lambda: imap)
        self.assertEqual(rows['total'], 3)
        self.assertEqual([row['subject'] for row in rows['messages']], ['第三封', '第二封'])
        self.assertEqual(rows['messages'][0]['sender'], 'sender@example.com')
        self.assertTrue(imap.closed)

    def test_empty_mailbox_and_invalid_count(self):
        imap = FakeIMAP(search_results=[b''])
        self.assertEqual(app.list_latest('a', 'b', 5, lambda: imap), {'total': 0, 'matched': 0, 'messages': []})
        with self.assertRaises(app.AppError):
            app.list_latest('a', 'b', 0, lambda: imap)

    def test_send_uses_unique_id_and_closes_connection(self):
        smtp = FakeSMTP()
        message_id = app.send_mail('me@example.com', 'secret', 'friend@example.com', '測試', '內文', lambda: smtp)
        self.assertEqual(smtp.messages[0]['To'], 'friend@example.com')
        self.assertEqual(smtp.messages[0]['Message-ID'], message_id)
        self.assertTrue(smtp.closed)
        self.assertNotIn('secret', smtp.messages[0].as_string())

    def test_verify_sends_to_self_then_searches_exact_message_id(self):
        smtp = FakeSMTP()
        imap = FakeIMAP(search_results=[b'', b'42'])
        times = iter([0, 1, 2])
        elapsed = app.verify_delivery('me@example.com', 'secret', '主旨', '內文', 10, 1,
                                      lambda: imap, lambda: smtp, lambda: next(times), lambda _: None)
        self.assertEqual(elapsed, 2)
        self.assertEqual(smtp.messages[0]['To'], 'me@example.com')
        self.assertIn(smtp.messages[0]['Message-ID'], str(imap.searches))
        self.assertTrue(imap.closed)

    def test_match_after_deadline_is_timeout(self):
        imap, smtp = FakeIMAP(search_results=[b'42']), FakeSMTP()
        times = iter([0, 3])
        with self.assertRaises(app.DeliveryTimeout):
            app.verify_delivery('me@example.com', 'secret', 'subject', 'body', timeout=2,
                                imap_factory=lambda: imap, smtp_factory=lambda: smtp,
                                clock=lambda: next(times))
        self.assertTrue(imap.closed)

    def test_match_at_deadline_is_success(self):
        imap, smtp = FakeIMAP(search_results=[b'42']), FakeSMTP()
        times = iter([0, 2])
        self.assertEqual(app.verify_delivery('me@example.com', 'secret', 'subject', 'body', timeout=2,
                                            imap_factory=lambda: imap, smtp_factory=lambda: smtp,
                                            clock=lambda: next(times)), 2)

    def test_verify_timeout_is_not_success(self):
        imap, smtp = FakeIMAP(search_results=[b'', b'']), FakeSMTP()
        times = iter([0, 0, 1, 1, 2])
        with self.assertRaises(app.DeliveryTimeout):
            app.verify_delivery('me@example.com', 'secret', '主旨', '內文', 2, 1,
                                lambda: imap, lambda: smtp, lambda: next(times), lambda _: None)
        self.assertTrue(imap.closed)

    def test_invalid_addresses_do_not_open_smtp(self):
        for address in ('@example.com', 'a b@example.com', 'a@example..com', 'a..b@example.com',
                        'a@-example.com', 'a@example-.com', 'a@example.com, b@example.com'):
            with self.subTest(address=address), self.assertRaises(app.AppError):
                app.send_mail('me@example.com', 'secret', address, 'subject', 'body',
                              lambda: self.fail('invalid address must not connect'))

    def test_invalid_login_addresses_do_not_prompt(self):
        for address in ('@example.com', 'a b@example.com'):
            with self.subTest(address=address), patch.dict(os.environ, {'GMAIL_ADDRESS': address}, clear=True):
                with self.assertRaises(app.AppError):
                    app.load_settings(lambda _: self.fail('invalid address must not prompt'))

    def test_normal_plus_tag_recipient_is_accepted(self):
        smtp = FakeSMTP()
        app.send_mail('me@example.com', 'secret', 'person+test@example.com', 'subject', 'body', lambda: smtp)
        self.assertEqual(smtp.messages[0]['To'], 'person+test@example.com')

    def test_invalid_subject_does_not_connect_or_crash(self):
        with self.assertRaises(app.AppError):
            app.send_mail('me@example.com', 'secret', 'me@example.com', 'bad\nheader', 'body',
                          lambda: self.fail('must reject before connecting'))

    def test_refused_recipient_is_failure_and_connection_closes(self):
        smtp = FakeSMTP()
        smtp.send_message = lambda message: {'friend@example.com': (550, b'refused')}
        with self.assertRaises(app.AppError):
            app.send_mail('me@example.com', 'secret', 'friend@example.com', '測試', '內文', lambda: smtp)
        self.assertTrue(smtp.closed)

    def test_bad_server_status_closes_connection(self):
        imap = FakeIMAP()
        imap.select = lambda *a, **kw: ('NO', [b'failed'])
        with self.assertRaises(app.AppError):
            app.list_latest('a', 'b', 1, lambda: imap)
        self.assertTrue(imap.closed)

    def test_nonfinite_or_nonpositive_wait_is_rejected(self):
        for value in (0, -1, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(app.AppError):
                app.verify_delivery('me@example.com', 'b', 'subject', 'body', timeout=value, smtp_factory=lambda: self.fail('must validate before sending'))

    def test_result_record_masks_addresses_and_omits_secret_and_body(self):
        import tempfile
        import pathlib
        with tempfile.TemporaryDirectory() as folder:
            paths = app.save_result(folder, 'list', {'total': 1, 'messages': [
                {'date': '2026-01-01', 'sender': 'Alice <alice@example.com>', 'subject': '私人主旨'}]})
            contents = [pathlib.Path(p).read_text(encoding='utf-8') for p in paths]
            for content in contents:
                self.assertNotIn('alice@example.com', content)
                self.assertNotIn('私人主旨', content)
                self.assertIn('1', content)

    def test_command_handles_authentication_failure_without_traceback_or_password(self):
        with (patch.object(app, 'load_settings', return_value=('me@example.com', 'secret')),
             patch.object(app, 'list_latest', side_effect=app.imaplib.IMAP4.error('bad credentials')),
             patch('sys.stderr', new_callable=io.StringIO) as err):
            self.assertEqual(app.main(['list']), 1)
            self.assertIn('認證', err.getvalue())
            self.assertNotIn('secret', err.getvalue())
            self.assertNotIn('Traceback', err.getvalue())

    def test_network_failure_is_explained_without_raw_exception(self):
        with (patch.object(app, 'load_settings', return_value=('me@example.com', 'secret')),
             patch.object(app, 'list_latest', side_effect=OSError('private details')),
             patch('sys.stderr', new_callable=io.StringIO) as err):
            self.assertEqual(app.main(['list']), 1)
            self.assertIn('網路', err.getvalue())
            self.assertNotIn('private details', err.getvalue())


class ExtendedFeatureTests(unittest.TestCase):
    @staticmethod
    def mail(subject):
        message = EmailMessage()
        message['Subject'] = subject
        message['From'] = 'sender@example.com'
        message['Date'] = 'Fri, 01 Jan 2021 00:00:00 +0000'
        return message.as_bytes()

    def test_combined_filters_apply_before_limit_and_decode_subject(self):
        imap = FakeIMAP({b'1': self.mail('作業 一'), b'2': self.mail('不相符'),
                         b'3': self.mail('作業 三'), b'4': self.mail('其他')},
                        [b'1 2 3 4 5', b'1 2 3 4'])
        result = app.list_latest('me@example.com', 'secret', 1, lambda: imap,
                                 unread=True, since='2026-10-01', subject_keyword='作業')
        self.assertEqual(result['total'], 5)
        self.assertEqual(result['matched'], 2)
        self.assertEqual([x['subject'] for x in result['messages']], ['作業 三'])
        self.assertIn('UNSEEN', imap.searches[1])
        self.assertIn('SINCE', imap.searches[1])
        self.assertIn('01-Oct-2026', imap.searches[1])
        self.assertTrue(imap.closed)

    def test_no_matches_and_invalid_date(self):
        imap = FakeIMAP(search_results=[b'1', b''])
        result = app.list_latest('me@example.com', 'secret', 5, lambda: imap, unread=True)
        self.assertEqual((result['total'], result['matched'], result['messages']), (1, 0, []))
        with self.assertRaises(app.AppError):
            app.list_latest('me@example.com', 'secret', 5,
                            lambda: self.fail('invalid date must not connect'), since='2026-02-30')

    def test_subject_keyword_matches_case_insensitively(self):
        imap = FakeIMAP({b'1': self.mail('Gmail TEST')}, [b'1'])
        result = app.list_latest('me@example.com', 'secret', 1, lambda: imap, subject_keyword='test')
        self.assertEqual(result['matched'], 1)

    def test_send_attachment_content_and_filename(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '測試.txt'
            path.write_bytes('附件內容'.encode())
            smtp = FakeSMTP()
            app.send_mail('me@example.com', 'secret', 'me@example.com', '測試', '內文', lambda: smtp,
                          attachments=[str(path)])
            parts = list(smtp.messages[0].iter_attachments())
            self.assertEqual(len(parts), 1)
            self.assertEqual(parts[0].get_filename(), '測試.txt')
            self.assertEqual(parts[0].get_payload(decode=True), '附件內容'.encode())

    def test_missing_attachment_does_not_connect_or_send(self):
        with self.assertRaises(app.AppError):
            app.send_mail('me@example.com', 'secret', 'me@example.com', 'subject', 'body',
                          lambda: self.fail('must not connect'), attachments=['/nonexistent/absent.txt'])

    def test_verify_passes_attachment_to_self_sent_message(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sample.txt'
            path.write_text('sample', encoding='utf-8')
            smtp, imap = FakeSMTP(), FakeIMAP(search_results=[b'42'])
            times = iter([0, 1])
            app.verify_delivery('me@example.com', 'secret', 'subject', 'body',
                                imap_factory=lambda: imap, smtp_factory=lambda: smtp,
                                clock=lambda: next(times), attachments=[str(path)])
            self.assertEqual(next(smtp.messages[0].iter_attachments()).get_payload(decode=True), b'sample')
            self.assertEqual(smtp.messages[0]['To'], 'me@example.com')

    def test_saved_result_records_filter_types_and_attachment_count_without_secrets(self):
        import tempfile
        import json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path, _ = app.save_result(folder, 'list', {'total': 4, 'matched': 2, 'messages': [],
                                      'filters': ['unread', 'since', 'subject_keyword']})
            record = json.loads(Path(path).read_text())
            self.assertEqual(record['filters'], ['unread', 'since', 'subject_keyword'])
            self.assertEqual(record['matched'], 2)
            path, _ = app.save_result(folder, 'verify', {'elapsed_seconds': 1.0, 'attachment_count': 1})
            self.assertEqual(json.loads(Path(path).read_text())['attachment_count'], 1)

    def test_cli_accepts_extension_options(self):
        args = app.parser().parse_args(['list', '--unread', '--since', '2026-10-01', '--subject-keyword', 'test'])
        self.assertTrue(args.unread)
        self.assertEqual(args.since, '2026-10-01')
        args = app.parser().parse_args(['send', '--to', 'a@example.com', '--subject', 'x', '--body', 'y', '--attach', 'a.txt'])
        self.assertEqual(args.attach, ['a.txt'])


if __name__ == '__main__':
    unittest.main()
