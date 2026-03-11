import sqlite3
import os
import string
import random
import time
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'kintai.db')


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=30000')
    return conn


def db_write_with_retry(func, max_retries=3):
    """書き込み操作をリトライ付きで実行"""
    for attempt in range(max_retries):
        try:
            return func()
        except sqlite3.OperationalError as e:
            if 'locked' in str(e) and attempt < max_retries - 1:
                time.sleep(0.1 * (attempt + 1))
            else:
                raise


def init_db():
    conn = get_db()
    c = conn.cursor()

    # WALモードを永続的に設定（同時アクセス性能向上）
    c.execute('PRAGMA journal_mode=WAL')

    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            login_id TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            workplace TEXT NOT NULL DEFAULT '',
            is_admin INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
    ''')

    # 既存DBにis_activeカラムがない場合は追加（マイグレーション）
    existing_columns = [row[1] for row in c.execute('PRAGMA table_info(users)').fetchall()]
    if 'is_active' not in existing_columns:
        c.execute('ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1')

    c.execute('''
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            clock_in TEXT,
            clock_out TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, date)
        )
    ''')

    # 管理者アカウントが存在しなければ作成
    admin = c.execute('SELECT id FROM users WHERE is_admin = 1').fetchone()
    if not admin:
        c.execute(
            'INSERT INTO users (login_id, password_hash, name, workplace, is_admin) VALUES (?, ?, ?, ?, ?)',
            ('admin', generate_password_hash('admin123'), '管理者', '本社', 1)
        )

    conn.commit()
    conn.close()


def generate_login_id():
    """6桁のランダムなログインIDを生成"""
    return ''.join(random.choices(string.digits, k=6))


def generate_password():
    """8文字のランダムなパスワードを生成"""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=8))


def create_employee(name, workplace):
    """従業員を作成し、ログインID・パスワードを返す"""
    raw_password = generate_password()
    password_hash = generate_password_hash(raw_password)

    def _do():
        conn = get_db()
        try:
            c = conn.cursor()
            while True:
                login_id = generate_login_id()
                existing = c.execute('SELECT id FROM users WHERE login_id = ?', (login_id,)).fetchone()
                if not existing:
                    break

            c.execute(
                'INSERT INTO users (login_id, password_hash, name, workplace, is_admin) VALUES (?, ?, ?, ?, ?)',
                (login_id, password_hash, name, workplace, 0)
            )
            conn.commit()
            return login_id, raw_password
        finally:
            conn.close()

    return db_write_with_retry(_do)


def authenticate(login_id, password):
    """認証。成功すればユーザー情報を返す（無効化ユーザーは拒否）"""
    conn = get_db()
    user = conn.execute(
        'SELECT * FROM users WHERE login_id = ? AND is_active = 1', (login_id,)
    ).fetchone()
    conn.close()
    if user and check_password_hash(user['password_hash'], password):
        return dict(user)
    return None


def clock_in(user_id):
    """出勤打刻（何度でも上書き可能）"""
    now = datetime.now()
    date_str = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H:%M:%S')

    def _do():
        conn = get_db()
        try:
            existing = conn.execute(
                'SELECT * FROM attendance WHERE user_id = ? AND date = ?',
                (user_id, date_str)
            ).fetchone()

            if existing:
                conn.execute(
                    'UPDATE attendance SET clock_in = ? WHERE user_id = ? AND date = ?',
                    (time_str, user_id, date_str)
                )
            else:
                conn.execute(
                    'INSERT INTO attendance (user_id, date, clock_in) VALUES (?, ?, ?)',
                    (user_id, date_str, time_str)
                )
            conn.commit()
        finally:
            conn.close()
        return True, time_str

    return db_write_with_retry(_do)


def clock_out(user_id):
    """退勤打刻（何度でも上書き可能、出勤なしでも可）"""
    now = datetime.now()
    date_str = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H:%M:%S')

    def _do():
        conn = get_db()
        try:
            existing = conn.execute(
                'SELECT * FROM attendance WHERE user_id = ? AND date = ?',
                (user_id, date_str)
            ).fetchone()

            if existing:
                conn.execute(
                    'UPDATE attendance SET clock_out = ? WHERE user_id = ? AND date = ?',
                    (time_str, user_id, date_str)
                )
            else:
                conn.execute(
                    'INSERT INTO attendance (user_id, date, clock_out) VALUES (?, ?, ?)',
                    (user_id, date_str, time_str)
                )
            conn.commit()
        finally:
            conn.close()
        return True, time_str

    return db_write_with_retry(_do)


def calc_break_time(work_minutes):
    """労働基準法に基づく休憩時間(分)を自動計算"""
    if work_minutes > 480:  # 8時間超
        return 60
    elif work_minutes > 360:  # 6時間超
        return 45
    else:
        return 0


def get_attendance_records(user_id=None, year_month=None):
    """勤怠レコード取得。user_id/year_monthでフィルタ可能"""
    conn = get_db()
    query = '''
        SELECT a.date, a.clock_in, a.clock_out,
               a.user_id, u.name, u.workplace, u.login_id
        FROM attendance a
        JOIN users u ON a.user_id = u.id
        WHERE 1=1
    '''
    params = []

    if user_id:
        query += ' AND a.user_id = ?'
        params.append(user_id)

    if year_month:
        query += " AND a.date LIKE ?"
        params.append(f'{year_month}%')

    query += ' ORDER BY a.date ASC, u.name'

    rows = conn.execute(query, params).fetchall()
    conn.close()

    records = []
    for row in rows:
        r = dict(row)
        # 労働時間・休憩時間の計算
        if r['clock_in'] and r['clock_out']:
            fmt = '%H:%M:%S'
            t_in = datetime.strptime(r['clock_in'], fmt)
            t_out = datetime.strptime(r['clock_out'], fmt)
            diff = t_out - t_in
            if diff.total_seconds() < 0:
                diff += timedelta(days=1)
            total_minutes = diff.total_seconds() / 60
            break_min = calc_break_time(total_minutes)
            work_minutes = total_minutes - break_min
            r['break_time'] = f'{break_min}分'
            r['work_hours'] = f'{int(work_minutes // 60)}時間{int(work_minutes % 60)}分'
            r['total_raw_minutes'] = total_minutes
        else:
            r['break_time'] = '-'
            r['work_hours'] = '-'
            r['total_raw_minutes'] = 0
        records.append(r)

    return records


def get_all_employees():
    """有効な従業員(管理者以外)を取得"""
    conn = get_db()
    rows = conn.execute(
        'SELECT id, login_id, name, workplace FROM users WHERE is_admin = 0 AND is_active = 1 ORDER BY name'
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_employee(user_id):
    """従業員を削除（勤怠データは保持）"""
    def _do():
        conn = get_db()
        try:
            conn.execute(
                'UPDATE users SET is_active = 0 WHERE id = ? AND is_admin = 0',
                (user_id,)
            )
            conn.commit()
        finally:
            conn.close()

    db_write_with_retry(_do)


def is_user_active(user_id):
    """ユーザーが有効かどうかを確認"""
    conn = get_db()
    user = conn.execute(
        'SELECT is_active FROM users WHERE id = ?', (user_id,)
    ).fetchone()
    conn.close()
    return user is not None and user['is_active'] == 1


def get_today_status(user_id):
    """本日の打刻状態と時刻を取得"""
    conn = get_db()
    date_str = datetime.now().strftime('%Y-%m-%d')
    row = conn.execute(
        'SELECT * FROM attendance WHERE user_id = ? AND date = ?',
        (user_id, date_str)
    ).fetchone()
    conn.close()
    if not row:
        return {'status': 'not_clocked_in', 'clock_in': None, 'clock_out': None}
    if row['clock_out']:
        return {'status': 'clocked_out', 'clock_in': row['clock_in'], 'clock_out': row['clock_out']}
    return {'status': 'clocked_in', 'clock_in': row['clock_in'], 'clock_out': None}


def reset_password(user_id):
    """パスワードをリセットして新しいパスワードを返す"""
    new_password = generate_password()
    new_hash = generate_password_hash(new_password)

    def _do():
        conn = get_db()
        try:
            conn.execute(
                'UPDATE users SET password_hash = ? WHERE id = ?',
                (new_hash, user_id)
            )
            conn.commit()
        finally:
            conn.close()

    db_write_with_retry(_do)
    return new_password


def update_employee(user_id, name, workplace):
    """従業員情報を更新"""
    def _do():
        conn = get_db()
        try:
            conn.execute(
                'UPDATE users SET name = ?, workplace = ? WHERE id = ? AND is_admin = 0',
                (name, workplace, user_id)
            )
            conn.commit()
        finally:
            conn.close()

    db_write_with_retry(_do)


def normalize_time(time_str):
    """時刻をHH:MM:SS形式に正規化"""
    if not time_str:
        return None
    # HH:MM → HH:MM:00 に変換
    if len(time_str) == 5:
        return time_str + ':00'
    return time_str


def update_attendance(user_id, date, clock_in, clock_out):
    """勤怠データを手動修正"""
    clock_in = normalize_time(clock_in)
    clock_out = normalize_time(clock_out)

    def _do():
        conn = get_db()
        try:
            existing = conn.execute(
                'SELECT * FROM attendance WHERE user_id = ? AND date = ?',
                (user_id, date)
            ).fetchone()

            if existing:
                conn.execute(
                    'UPDATE attendance SET clock_in = ?, clock_out = ? WHERE user_id = ? AND date = ?',
                    (clock_in, clock_out, user_id, date)
                )
            else:
                conn.execute(
                    'INSERT INTO attendance (user_id, date, clock_in, clock_out) VALUES (?, ?, ?, ?)',
                    (user_id, date, clock_in, clock_out)
                )
            conn.commit()
        finally:
            conn.close()

    db_write_with_retry(_do)


def get_today_overview():
    """本日の有効な従業員の出勤状況を取得"""
    conn = get_db()
    date_str = datetime.now().strftime('%Y-%m-%d')
    rows = conn.execute('''
        SELECT u.name, u.workplace, a.clock_in, a.clock_out
        FROM users u
        LEFT JOIN attendance a ON u.id = a.user_id AND a.date = ?
        WHERE u.is_admin = 0 AND u.is_active = 1
        ORDER BY u.workplace, u.name
    ''', (date_str,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_workplaces():
    """有効な従業員の勤務先一覧を取得"""
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT workplace FROM users WHERE is_admin = 0 AND is_active = 1 AND workplace != '' ORDER BY workplace"
    ).fetchall()
    conn.close()
    return [r['workplace'] for r in rows]
