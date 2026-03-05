"""勤怠システム 自動テスト"""
import os
import sys
import tempfile

# テスト用の一時DBを使う
TEST_DB = os.path.join(tempfile.gettempdir(), 'kintai_test.db')
if os.path.exists(TEST_DB):
    os.remove(TEST_DB)

import database as db
db.DB_PATH = TEST_DB
db.init_db()

from app import app
app.config['TESTING'] = True
app.config['WTF_CSRF_ENABLED'] = False

client = app.test_client()

passed = 0
failed = 0

def test(name, condition, detail=''):
    global passed, failed
    if condition:
        print(f'  OK: {name}')
        passed += 1
    else:
        print(f'  NG: {name} - {detail}')
        failed += 1


print('=== 1. ログイン機能 ===')

# 未ログインでトップページ → ログインにリダイレクト
r = client.get('/', follow_redirects=False)
test('未ログイン → リダイレクト', r.status_code in (302, 308))

# 間違ったパスワードでログイン
r = client.post('/login', data={'login_id': 'admin', 'password': 'wrong'}, follow_redirects=True)
test('誤パスワード → エラー表示', 'IDまたはパスワードが間違っています'.encode('utf-8') in r.data)

# 正しいパスワードで管理者ログイン
r = client.post('/login', data={'login_id': 'admin', 'password': 'admin123'}, follow_redirects=True)
test('管理者ログイン成功', r.status_code == 200)
test('管理画面が表示される', '管理画面'.encode('utf-8') in r.data)

# ログアウト
r = client.get('/logout', follow_redirects=True)
test('ログアウト → ログイン画面', 'ログイン'.encode('utf-8') in r.data)


print('\n=== 2. 従業員登録 ===')

# 管理者で再ログイン
client.post('/login', data={'login_id': 'admin', 'password': 'admin123'})

# 従業員登録
r = client.post('/admin/add_employee', data={'name': 'Nguyen Van A', 'workplace': '東京工場'}, follow_redirects=True)
test('従業員登録成功', '従業員を登録しました'.encode('utf-8') in r.data)
test('ID表示あり', 'ID:'.encode('utf-8') in r.data)
test('パスワード表示あり', 'パスワード:'.encode('utf-8') in r.data or 'パスワード'.encode('utf-8') in r.data)

# 2人目の従業員
r = client.post('/admin/add_employee', data={'name': 'Tran Thi B', 'workplace': '大阪工場'}, follow_redirects=True)
test('2人目の従業員登録', '従業員を登録しました'.encode('utf-8') in r.data)

# 名前なしで登録 → エラー
r = client.post('/admin/add_employee', data={'name': '', 'workplace': '東京'}, follow_redirects=True)
test('名前なし → エラー', '名前を入力してください'.encode('utf-8') in r.data)

# 従業員一覧の確認
employees = db.get_all_employees()
test('従業員が2名登録されている', len(employees) == 2, f'実際: {len(employees)}名')

emp1 = employees[0]
emp2 = employees[1]


print('\n=== 3. 従業員ログイン・打刻 ===')

# 管理者ログアウト
client.get('/logout')

# 従業員1でログイン
emp1_data = db.get_all_employees()[0]
# パスワードはハッシュ化されているので、新しい従業員を作って平文パスワードを取得
client.post('/login', data={'login_id': 'admin', 'password': 'admin123'})
login_id, raw_pw = db.create_employee('Test Worker', '名古屋工場')
client.get('/logout')

r = client.post('/login', data={'login_id': login_id, 'password': raw_pw}, follow_redirects=True)
test('従業員ログイン成功', r.status_code == 200)
test('打刻画面が表示される', '出勤 / Clock In'.encode('utf-8') in r.data)
test('勤務先が表示される', '名古屋工場'.encode('utf-8') in r.data)
test('未出勤ステータス', 'Not Clocked In'.encode('utf-8') in r.data)

# 出勤打刻
r = client.post('/clock', data={'action': 'clock_in'}, follow_redirects=True)
test('出勤打刻成功', '出勤しました'.encode('utf-8') in r.data)
test('出勤中ステータスに変更', 'Working'.encode('utf-8') in r.data)

# 出勤時刻が表示されている（--:--:--は時計初期値+退勤未打刻の2箇所のみ）
test('出勤時刻が表示される', r.data.count(b'--:--:--') == 2, '時計初期値+退勤の2箇所であるべき')

# 出勤を再度押す → 確認ダイアログ用のJS変数がtrueになっている
test('出勤済みフラグがJSに渡る', b'const clockedIn = true' in r.data)

# 退勤打刻
r = client.post('/clock', data={'action': 'clock_out'}, follow_redirects=True)
test('退勤打刻成功', '退勤しました'.encode('utf-8') in r.data)
test('退勤済ステータス', 'Done'.encode('utf-8') in r.data)

# 出勤を再打刻（上書き可能か）
r = client.post('/clock', data={'action': 'clock_in'}, follow_redirects=True)
test('出勤再打刻（上書き）成功', '出勤しました'.encode('utf-8') in r.data)

# 退勤を再打刻
r = client.post('/clock', data={'action': 'clock_out'}, follow_redirects=True)
test('退勤再打刻（上書き）成功', '退勤しました'.encode('utf-8') in r.data)

client.get('/logout')


print('\n=== 4. 管理画面 - データ表示 ===')

client.post('/login', data={'login_id': 'admin', 'password': 'admin123'})

# 管理画面に出勤状況が表示される
r = client.get('/admin')
test('本日の出勤状況セクション表示', '本日の出勤状況'.encode('utf-8') in r.data)
test('従業員一覧に人数表示', '3名'.encode('utf-8') in r.data, f'従業員数: {len(db.get_all_employees())}')
test('勤怠データセクション表示', '勤怠データ'.encode('utf-8') in r.data)
test('CSVダウンロードリンク', 'CSV'.encode('utf-8') in r.data)
test('Excelダウンロードリンク', 'Excel'.encode('utf-8') in r.data)


print('\n=== 5. 勤務先フィルタ ===')

workplaces = db.get_all_workplaces()
test('勤務先一覧取得', len(workplaces) >= 2, f'取得: {workplaces}')

r = client.get('/admin?workplace=東京工場')
test('勤務先フィルタが機能する', r.status_code == 200)


print('\n=== 6. 従業員編集 ===')

emp = db.get_all_employees()[0]
r = client.post(f'/admin/edit_employee/{emp["id"]}', data={'name': 'Updated Name', 'workplace': '福岡工場'}, follow_redirects=True)
test('従業員編集成功', '情報を更新しました'.encode('utf-8') in r.data)

updated = db.get_all_employees()
found = [e for e in updated if e['name'] == 'Updated Name']
test('名前が更新されている', len(found) == 1)
test('勤務先が更新されている', found[0]['workplace'] == '福岡工場' if found else False)


print('\n=== 7. パスワードリセット ===')

emp = db.get_all_employees()[0]
r = client.post(f'/admin/reset_password/{emp["id"]}', follow_redirects=True)
test('PWリセット成功', 'パスワードをリセットしました'.encode('utf-8') in r.data)
test('新パスワード表示あり', '新パスワード'.encode('utf-8') in r.data)


print('\n=== 8. 勤怠データ手動修正 ===')

emp = db.get_all_employees()[0]
r = client.post('/admin/edit_attendance', data={
    'user_id': emp['id'],
    'date': '2026-03-01',
    'clock_in': '09:00',
    'clock_out': '18:00'
}, follow_redirects=True)
test('勤怠手動追加成功', '勤怠データを更新しました'.encode('utf-8') in r.data)

# DBに正しい形式で保存されたか
records = db.get_attendance_records(user_id=emp['id'], year_month='2026-03')
r_march1 = [r for r in records if r['date'] == '2026-03-01']
test('手動追加レコード存在', len(r_march1) == 1)
if r_march1:
    test('出勤時刻がHH:MM:SS形式', r_march1[0]['clock_in'] == '09:00:00', f'実際: {r_march1[0]["clock_in"]}')
    test('退勤時刻がHH:MM:SS形式', r_march1[0]['clock_out'] == '18:00:00', f'実際: {r_march1[0]["clock_out"]}')
    test('休憩60分（8h超）', r_march1[0]['break_time'] == '60分', f'実際: {r_march1[0]["break_time"]}')
    test('労働時間8時間0分', r_march1[0]['work_hours'] == '8時間0分', f'実際: {r_march1[0]["work_hours"]}')

# 手動修正（上書き）
r = client.post('/admin/edit_attendance', data={
    'user_id': emp['id'],
    'date': '2026-03-01',
    'clock_in': '08:30',
    'clock_out': '17:30'
}, follow_redirects=True)
test('勤怠修正（上書き）成功', '勤怠データを更新しました'.encode('utf-8') in r.data)

# 日付なしで送信 → エラー
r = client.post('/admin/edit_attendance', data={
    'user_id': emp['id'],
    'date': '',
    'clock_in': '09:00',
    'clock_out': '18:00'
}, follow_redirects=True)
test('日付なし → エラー', '日付を入力してください'.encode('utf-8') in r.data)


print('\n=== 9. CSVダウンロード ===')

r = client.get('/admin/download?format=csv&year_month=2026-03')
test('CSVダウンロード成功', r.status_code == 200)
test('CSVのContent-Type', 'text/csv' in r.content_type)
csv_data = r.data.decode('utf-8-sig')
test('CSVにヘッダーあり', '日付' in csv_data and '名前' in csv_data)
test('CSVにデータあり', '2026-03-01' in csv_data)


print('\n=== 10. Excelダウンロード ===')

r = client.get('/admin/download?format=excel&year_month=2026-03')
test('Excelダウンロード成功', r.status_code == 200)
test('ExcelのContent-Type', 'spreadsheet' in r.content_type)
test('Excelファイルサイズ > 0', len(r.data) > 100)


print('\n=== 11. 休憩時間計算 ===')

test('6時間以下 → 0分', db.calc_break_time(360) == 0)
test('6時間超(361分) → 45分', db.calc_break_time(361) == 45)
test('8時間(480分) → 45分', db.calc_break_time(480) == 45)
test('8時間超(481分) → 60分', db.calc_break_time(481) == 60)
test('10時間(600分) → 60分', db.calc_break_time(600) == 60)


print('\n=== 12. 日跨ぎ勤務の計算 ===')

emp = db.get_all_employees()[0]
db.update_attendance(emp['id'], '2026-03-02', '22:00', '07:00')
records = db.get_attendance_records(user_id=emp['id'], year_month='2026-03')
night = [r for r in records if r['date'] == '2026-03-02']
test('日跨ぎレコード存在', len(night) == 1)
if night:
    test('日跨ぎ: 休憩60分(9h)', night[0]['break_time'] == '60分', f'実際: {night[0]["break_time"]}')
    test('日跨ぎ: 労働時間8時間0分', night[0]['work_hours'] == '8時間0分', f'実際: {night[0]["work_hours"]}')


print('\n=== 13. 時刻フォーマット正規化 ===')

test('HH:MM → HH:MM:00', db.normalize_time('09:00') == '09:00:00')
test('HH:MM:SS → そのまま', db.normalize_time('09:00:30') == '09:00:30')
test('空文字 → None', db.normalize_time('') is None)
test('None → None', db.normalize_time(None) is None)


print('\n=== 14. 従業員削除 ===')

emp = db.get_all_employees()[-1]
emp_count_before = len(db.get_all_employees())
r = client.post(f'/admin/delete_employee/{emp["id"]}', follow_redirects=True)
test('従業員削除成功', '従業員を削除しました'.encode('utf-8') in r.data)
test('従業員数が1減った', len(db.get_all_employees()) == emp_count_before - 1)


print('\n=== 15. セキュリティ ===')

client.get('/logout')

# 未ログインで管理画面アクセス → リダイレクト
r = client.get('/admin', follow_redirects=False)
test('未ログイン → 管理画面拒否', r.status_code in (302, 308))

# 未ログインで打刻画面アクセス → リダイレクト
r = client.get('/clock', follow_redirects=False)
test('未ログイン → 打刻画面拒否', r.status_code in (302, 308))

# 従業員で管理画面アクセス → リダイレクト
client.post('/login', data={'login_id': login_id, 'password': raw_pw})
r = client.get('/admin', follow_redirects=False)
test('従業員 → 管理画面拒否', r.status_code in (302, 308))

# 従業員でAPI直叩き → リダイレクト
r = client.post('/admin/add_employee', data={'name': 'Hack', 'workplace': 'x'}, follow_redirects=False)
test('従業員 → 従業員登録API拒否', r.status_code in (302, 308))


print('\n' + '=' * 40)
print(f'結果: {passed} passed / {failed} failed / {passed + failed} total')
if failed == 0:
    print('全テスト合格!')
else:
    print(f'** {failed}件の失敗あり **')

# テストDB削除
os.remove(TEST_DB)
