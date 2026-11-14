#!/usr/bin/env python3
"""以 Gmail IMAP/SMTP 讀信、寄信及驗證自寄信。"""
import argparse
import getpass
import hashlib
import imaplib
import json
import math
import mimetypes
import os
import re
import smtplib
import ssl
import sys
import time
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, make_msgid


class AppError(Exception):
    """可安全顯示給使用者的錯誤。"""


class DeliveryTimeout(AppError):
    pass


def valid_address(address):
    """檢查常見 ASCII 郵件地址格式，不保證收件信箱存在。"""
    if not isinstance(address, str) or len(address) > 254 or address.count('@') != 1:
        return False
    local, domain = address.rsplit('@', 1)
    if not local or len(local) > 64 or not domain or len(domain) > 253:
        return False
    atom = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    if not re.fullmatch(rf"{atom}(?:\.{atom})*", local):
        return False
    labels = domain.split('.')
    return all(len(label) <= 63 and re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?', label)
               for label in labels)


def load_settings(prompt=getpass.getpass):
    address = os.environ.get('GMAIL_ADDRESS', '').strip()
    if not valid_address(address):
        raise AppError('請設定有效的 GMAIL_ADDRESS 環境變數。')
    password = os.environ.get('GMAIL_APP_PASSWORD')
    if password is None:
        password = prompt('Gmail 應用程式密碼（不顯示）：')
    if not password or not password.strip():
        raise AppError('應用程式密碼不可為空。')
    password = ''.join(password.split())
    if not password.isascii():
        raise AppError('應用程式密碼只能包含 ASCII 字元；請確認輸入法及密碼。')
    return address, password


def open_imap():
    return imaplib.IMAP4_SSL('imap.gmail.com', 993, ssl_context=ssl.create_default_context(), timeout=15)


def open_smtp():
    return smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ssl.create_default_context(), timeout=15)


def checked(result, action):
    status, data = result
    if status != 'OK':
        raise AppError(f'{action}失敗，請檢查 Gmail 設定後重試。')
    return data


def close_quietly(connection, method):
    try:
        getattr(connection, method)()
    except (OSError, imaplib.IMAP4.error, smtplib.SMTPException, EOFError):
        pass


def connect_inbox(address, password, factory):
    client = factory()
    try:
        checked(client.login(address, password), 'IMAP 登入')
        checked(client.select('INBOX', readonly=True), '開啟收件匣')
        return client
    except BaseException:
        close_quietly(client, 'logout')
        raise


def list_latest(address, password, count, imap_factory=open_imap,
                unread=False, since=None, subject_keyword=None):
    if count < 1:
        raise AppError('讀取封數必須大於 0。')
    criteria = []
    if unread:
        criteria.append('UNSEEN')
    if since is not None:
        try:
            parsed = date.fromisoformat(since)
            if parsed.isoformat() != since:
                raise ValueError
        except (ValueError, TypeError):
            raise AppError('日期格式須為有效的 YYYY-MM-DD。') from None
        months = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
        criteria.extend(('SINCE', f'{parsed.day:02d}-{months[parsed.month - 1]}-{parsed.year:04d}'))
    if subject_keyword is not None and not subject_keyword.strip():
        raise AppError('主旨關鍵字不可為空。')
    client = connect_inbox(address, password, imap_factory)
    try:
        data = checked(client.uid('search', None, 'ALL'), '搜尋信件')
        all_ids = data[0].split() if data and data[0] else []
        if criteria:
            data = checked(client.uid('search', None, *criteria), '篩選信件')
            ids = data[0].split() if data and data[0] else []
        else:
            ids = all_ids

        def read_header(uid):
            data = checked(client.uid('fetch', uid, '(BODY.PEEK[HEADER.FIELDS (DATE FROM SUBJECT)])'), '讀取信件')
            raw = next((item[1] for item in data if isinstance(item, tuple) and isinstance(item[1], bytes)), None)
            if raw is None:
                raise AppError('無法解析信件標頭，請重試。')
            msg = BytesParser(policy=policy.default).parsebytes(raw)
            return {'date': str(msg.get('Date', '（無日期）')),
                    'sender': str(msg.get('From', '（無寄件者）')),
                    'subject': str(msg.get('Subject', '（無主旨）'))}

        if subject_keyword is None:
            matched = len(ids)
            rows = [read_header(uid) for uid in reversed(ids[-count:])]
        else:
            keyword = subject_keyword.casefold()
            rows = []
            matched = 0
            for uid in reversed(ids):
                row = read_header(uid)
                if keyword in row['subject'].casefold():
                    matched += 1
                    if len(rows) < count:
                        rows.append(row)
        return {'total': len(all_ids), 'matched': matched, 'messages': rows}
    finally:
        close_quietly(client, 'logout')


def send_mail(address, password, recipient, subject, body, smtp_factory=open_smtp, attachments=None):
    if not valid_address(recipient):
        raise AppError('請提供有效的單一收件信箱。')
    if not subject.strip() or '\n' in subject or '\r' in subject:
        raise AppError('主旨不可為空，也不可包含換行。')
    message = EmailMessage()
    message['From'], message['To'], message['Subject'] = address, recipient, subject
    message['Message-ID'] = make_msgid()
    message.set_content(body)
    for filename in attachments or []:
        path = Path(filename)
        try:
            content = path.read_bytes()
        except (OSError, ValueError):
            raise AppError('無法讀取附件；請確認檔案存在且可讀取。') from None
        mime, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (mime or 'application/octet-stream').split('/', 1)
        try:
            message.add_attachment(content, maintype=maintype, subtype=subtype, filename=path.name)
        except ValueError:
            raise AppError('附件檔名無效。') from None
    client = smtp_factory()
    try:
        client.login(address, password)
        refused = client.send_message(message)
        if refused:
            raise AppError('收件者遭 SMTP 伺服器拒絕，信件未確認寄出。')
        return str(message['Message-ID'])
    finally:
        close_quietly(client, 'quit')


def verify_delivery(address, password, subject, body, timeout=120, interval=5,
                    imap_factory=open_imap, smtp_factory=open_smtp,
                    clock=time.monotonic, sleep=time.sleep, attachments=None):
    if not math.isfinite(timeout) or not math.isfinite(interval) or timeout <= 0 or interval <= 0:
        raise AppError('等待時間與查詢間隔必須大於 0。')
    start = clock()
    message_id = send_mail(address, password, address, subject, body, smtp_factory, attachments=attachments)
    client = connect_inbox(address, password, imap_factory)
    try:
        while True:
            # 在每次查詢前重新選擇收件匣，讓伺服器反映新進郵件。
            checked(client.select('INBOX', readonly=True), '更新收件匣')
            data = checked(client.uid('search', None, 'HEADER', 'Message-ID', f'"{message_id}"'), '搜尋驗證信')
            now = clock()
            if data and data[0] and data[0].split() and now - start <= timeout:
                return now - start
            remaining = timeout - (now - start)
            if remaining <= 0:
                raise DeliveryTimeout(f'已寄送，但 {timeout:g} 秒內未在收件匣確認收到；請稍後自行檢查。')
            sleep(min(interval, remaining))
    finally:
        close_quietly(client, 'logout')


def find_unique_mail(address, password, subject, unread=False, include_body=False, imap_factory=open_imap):
    """以唯讀方式尋找主旨完全相符的唯一信件。"""
    if not subject.strip() or '\n' in subject or '\r' in subject:
        raise AppError('請提供有效的完整主旨。')
    client = connect_inbox(address, password, imap_factory)
    try:
        data = checked(client.uid('search', None, 'UNSEEN' if unread else 'ALL'), '搜尋信件')
        ids = data[0].split() if data and data[0] else []
        matches = []
        for uid in ids:
            result = checked(client.uid('fetch', uid, '(BODY.PEEK[HEADER])'), '讀取信件標頭')
            raw = next((part[1] for part in result if isinstance(part, tuple) and isinstance(part[1], bytes)), None)
            if raw is None:
                raise AppError('無法讀取信件標頭，請重試。')
            message = BytesParser(policy=policy.default).parsebytes(raw)
            if str(message.get('Subject', '')) == subject:
                matches.append((uid, message))
                if len(matches) > 1:
                    raise AppError('有多封信的主旨相同；請改用唯一主旨的測試信。')
        if not matches:
            raise AppError('沒有找到符合條件的信件。')
        uid, message = matches[0]
        validity = None
        if unread:
            _, values = client.response('UIDVALIDITY')
            validity = values[0] if values else None
            if not isinstance(validity, bytes) or not validity.isdigit() or not uid.isdigit():
                raise AppError('無法確認信件識別資訊，已停止回覆。')
        if include_body:
            result = checked(client.uid('fetch', uid, '(BODY.PEEK[])'), '讀取信件')
            raw = next((part[1] for part in result if isinstance(part, tuple) and isinstance(part[1], bytes)), None)
            if raw is None:
                raise AppError('無法讀取完整信件，請重試。')
            return uid, validity, raw
        return uid, validity, message
    finally:
        close_quietly(client, 'logout')


def export_message(address, password, subject, output, imap_factory=open_imap):
    output = Path(output)
    if os.path.lexists(output):
        raise AppError('輸出檔案已存在，請選擇其他路徑。')
    _, _, raw = find_unique_mail(address, password, subject, include_body=True, imap_factory=imap_factory)
    try:
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        raise AppError('無法建立輸出檔案；請確認目錄存在且檔案不存在。') from None
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(raw)
    except OSError:
        try:
            output.unlink()
        except OSError:
            pass
        raise AppError('信件存檔失敗，請檢查檔案權限與空間。') from None
    return len(raw)


def auto_reply_once(address, password, subject, body, guard_dir, imap_factory=open_imap, smtp_factory=open_smtp):
    if not body.strip():
        raise AppError('回覆內容不可為空。')
    uid, validity, original = find_unique_mail(address, password, subject, unread=True, imap_factory=imap_factory)
    senders = getaddresses([str(value) for value in original.get_all('From', [])])
    if len(senders) != 1 or senders[0][1].casefold() != address.casefold():
        raise AppError('僅可自動回覆本人寄來的測試信。')
    auto = str(original.get('Auto-Submitted', 'no')).strip().casefold()
    precedence = str(original.get('Precedence', '')).strip().casefold()
    if auto != 'no' or precedence in ('bulk', 'list', 'junk') or original.get('X-Auto-Response-Suppress'):
        raise AppError('自動產生或不接受自動回覆的信件不予回覆。')
    original_id = str(original.get('Message-ID', '')).strip()
    if not re.fullmatch(r'<[^<>\s@]+@[^<>\s@]+>', original_id) or not original_id.isascii():
        raise AppError('原信缺少可用的 Message-ID，無法回覆。')
    reply = EmailMessage()
    reply['From'], reply['To'] = address, address
    reply['Subject'] = subject if subject.casefold().startswith('re:') else 'Re: ' + subject
    reply['Message-ID'] = make_msgid()
    reply['In-Reply-To'] = original_id
    reply['References'] = original_id
    reply['Auto-Submitted'] = 'auto-replied'
    reply.set_content(body)
    key = hashlib.sha256(address.casefold().encode() + b'\0' + validity + b'\0' + uid).hexdigest()
    try:
        folder = Path(guard_dir)
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(folder / (key + '.guard'), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    except FileExistsError:
        raise AppError('此信件已處理或寄送狀態不明，請先檢查信箱，避免重複寄送。') from None
    except OSError:
        raise AppError('無法建立防重複紀錄，已停止回覆。') from None
    # 在連線前保留紀錄；若連線中斷，保守地視為可能已送達。
    smtp = smtp_factory()
    try:
        smtp.login(address, password)
        if smtp.send_message(reply):
            raise AppError('收件者遭 SMTP 伺服器拒絕，未確認寄出。')
    finally:
        close_quietly(smtp, 'quit')



def parser():
    p = argparse.ArgumentParser(description='Gmail 讀信、寄信與自寄驗證')
    sub = p.add_subparsers(dest='command', required=True)
    listing = sub.add_parser('list', help='列出最新信件標頭')
    listing.add_argument('-n', type=int, default=5, help='信件數（預設 5）')
    listing.add_argument('--unread', action='store_true', help='僅列出未讀信')
    listing.add_argument('--since', help='指定日期當日及之後（YYYY-MM-DD）')
    listing.add_argument('--subject-keyword', help='主旨包含的文字，不區分大小寫')
    send = sub.add_parser('send', help='寄信給自己或同組同學')
    send.add_argument('--to', required=True, help='單一收件信箱')
    verify = sub.add_parser('verify', help='寄給自己並驗證收到')
    export = sub.add_parser('export', help='依唯一完整主旨將信件存成 EML')
    export.add_argument('--subject', required=True, help='完整主旨')
    export.add_argument('--out', required=True, help='新的 .eml 檔案路徑')
    reply = sub.add_parser('auto-reply', help='單次回覆本人寄來的指定未讀信')
    reply.add_argument('--subject', required=True, help='原信完整主旨')
    reply.add_argument('--body', required=True, help='自動回覆內文')
    for item in (listing, send, verify, export, reply):
        item.add_argument('--save-result', action='store_true', help='儲存不含信箱、主旨或內文的執行紀錄')
    for item in (send, verify):
        item.add_argument('--subject', required=True, help='信件主旨')
        item.add_argument('--body', required=True, help='信件內文')
        item.add_argument('--attach', action='append', default=[], help='附件路徑，可重複使用')
        if item is verify:
            item.add_argument('--timeout', type=float, default=120, help='最長等待秒數（預設 120）')
            item.add_argument('--interval', type=float, default=5, help='查詢間隔秒數（預設 5）')
    return p


def save_result(folder, command, details):
    """只保存白名單資料，不保存信件標頭、信箱、密碼或內文。"""
    record = {'time': datetime.now().astimezone().isoformat(timespec='seconds'),
              'command': command, 'status': '成功'}
    if command == 'list':
        record.update(total=details['total'], matched=details.get('matched', details['total']), read_count=len(details['messages']))
        record['filters'] = [f for f in details.get('filters', []) if f in ('unread', 'since', 'subject_keyword')]
    elif command == 'verify':
        record.update(attachment_count=details.get('attachment_count', 0),
                      elapsed_seconds=round(details['elapsed_seconds'], 2),
                      observation='已於登入帳號收件匣找到本次自寄信')
    elif command == 'send':
        record['attachment_count'] = details.get('attachment_count', 0)
        record['observation'] = 'SMTP 已接受；尚未證明收件'
    elif command == 'export':
        record['observation'] = '已保存唯一符合的原始信件'
    elif command == 'auto-reply':
        record['observation'] = 'SMTP 已接受自動回覆；尚未證明收件'
    else:
        raise AppError('不支援的紀錄類型。')
    path = Path(folder)
    path.mkdir(parents=True, exist_ok=True)
    stem = 'run_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8]
    json_path, md_path = path / (stem + '.json'), path / (stem + '.md')
    with json_path.open('x', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
        f.write('\n')
    with md_path.open('x', encoding='utf-8') as f:
        f.write('# Gmail 執行紀錄\n\n')
        for key, value in record.items():
            f.write(f'- {key}: {value}\n')
    return json_path, md_path


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        address, password = load_settings()
        if args.command == 'list':
            result = list_latest(address, password, args.n, unread=args.unread, since=args.since, subject_keyword=args.subject_keyword)
            result['filters'] = [name for name, used in (('unread', args.unread), ('since', args.since),
                                                              ('subject_keyword', args.subject_keyword)) if used]
            rows = result['messages']
            print(f"收件匣總數：{result['total']}；符合條件：{result['matched']}；讀取筆數：{len(rows)}")
            if not rows:
                print('沒有符合條件的信件。')
            for i, row in enumerate(rows, 1):
                print(f"{i}. 日期：{row['date']}\n   寄件者：{row['sender']}\n   主旨：{row['subject']}")
        elif args.command == 'send':
            send_mail(address, password, args.to, args.subject, args.body, attachments=args.attach)
            result = {'attachment_count': len(args.attach)}
            print('SMTP 已接受信件；這不代表收件者已收到。')
        elif args.command == 'verify':
            elapsed = verify_delivery(address, password, args.subject, args.body, args.timeout, args.interval, attachments=args.attach)
            result = {'elapsed_seconds': elapsed, 'attachment_count': len(args.attach)}
            print(f'已在收件匣找到本次自寄信，耗時 {elapsed:.2f} 秒。')
        elif args.command == 'export':
            count = export_message(address, password, args.subject, args.out)
            result = {}
            print(f'已保存原始信件：{args.out}（{count} 位元組）。')
        else:
            auto_reply_once(address, password, args.subject, args.body,
                            Path(__file__).resolve().parent.parent / 'results' / '.auto_reply_guard')
            result = {}
            print('SMTP 已接受自動回覆；請在 Gmail 收件端確認。')
        if args.save_result:
            try:
                paths = save_result(Path(__file__).resolve().parent.parent / 'results', args.command, result)
                print('已儲存紀錄：' + '、'.join(str(p) for p in paths))
            except OSError:
                print('操作已完成，但無法儲存紀錄；請檢查 results 目錄權限。勿盲目重寄。', file=sys.stderr)
                return 1
        return 0
    except DeliveryTimeout as exc:
        print(f'未確認收件：{exc}', file=sys.stderr)
        return 2
    except AppError as exc:
        print(f'錯誤：{exc}', file=sys.stderr)
    except (imaplib.IMAP4.error, smtplib.SMTPAuthenticationError):
        print('Gmail 認證或 IMAP 操作失敗；請確認帳號、應用程式密碼及帳戶設定。', file=sys.stderr)
    except ssl.SSLError:
        print('TLS 安全連線失敗；請檢查系統時間、網路及憑證。', file=sys.stderr)
    except (OSError, EOFError, smtplib.SMTPException):
        print('網路或郵件伺服器連線失敗；若已寄出請先確認收件狀態再重試。', file=sys.stderr)
    except KeyboardInterrupt:
        print('\n已取消操作；若已寄出請先確認收件狀態再重試。', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
