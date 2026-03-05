import io
import os
from datetime import datetime
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, send_file)
from openpyxl import Workbook

import database as db

app = Flask(__name__)
app.secret_key = 'kintai-system-secret-key-2026'


# --- デコレータ ---

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session or not session.get('is_admin'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# --- ルート ---

@app.route('/')
def index():
    if 'user_id' in session:
        if session.get('is_admin'):
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('clock'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_id = request.form.get('login_id', '').strip()
        password = request.form.get('password', '').strip()

        user = db.authenticate(login_id, password)
        if user:
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['is_admin'] = bool(user['is_admin'])
            session['workplace'] = user['workplace']

            if user['is_admin']:
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('clock'))

        flash('IDまたはパスワードが間違っています', 'error')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/clock', methods=['GET', 'POST'])
@login_required
def clock():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'clock_in':
            success, msg = db.clock_in(session['user_id'])
            if success:
                flash(f'出勤しました ({msg})', 'success')
            else:
                flash(msg, 'error')

        elif action == 'clock_out':
            success, msg = db.clock_out(session['user_id'])
            if success:
                flash(f'退勤しました ({msg})', 'success')
            else:
                flash(msg, 'error')

        return redirect(url_for('clock'))

    today = db.get_today_status(session['user_id'])
    now = datetime.now()
    return render_template('clock.html', today=today, now=now)


@app.route('/admin')
@admin_required
def admin_dashboard():
    employees = db.get_all_employees()
    workplaces = db.get_all_workplaces()
    today_overview = db.get_today_overview()

    year_month = request.args.get('year_month', datetime.now().strftime('%Y-%m'))
    employee_filter = request.args.get('employee_id', '')
    workplace_filter = request.args.get('workplace', '')

    user_id = int(employee_filter) if employee_filter else None
    records = db.get_attendance_records(user_id=user_id, year_month=year_month)

    if workplace_filter:
        records = [r for r in records if r['workplace'] == workplace_filter]

    return render_template('admin.html',
                           employees=employees,
                           workplaces=workplaces,
                           today_overview=today_overview,
                           records=records,
                           year_month=year_month,
                           employee_filter=employee_filter,
                           workplace_filter=workplace_filter)


@app.route('/admin/add_employee', methods=['POST'])
@admin_required
def add_employee():
    name = request.form.get('name', '').strip()
    workplace = request.form.get('workplace', '').strip()

    if not name:
        flash('名前を入力してください', 'error')
        return redirect(url_for('admin_dashboard'))

    login_id, password = db.create_employee(name, workplace)
    flash(f'従業員を登録しました - 名前: {name} / ID: {login_id} / パスワード: {password}', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete_employee/<int:user_id>', methods=['POST'])
@admin_required
def delete_employee(user_id):
    db.delete_employee(user_id)
    flash('従業員を削除しました', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/reset_password/<int:user_id>', methods=['POST'])
@admin_required
def reset_password(user_id):
    new_password = db.reset_password(user_id)
    flash(f'パスワードをリセットしました - 新パスワード: {new_password}', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/edit_employee/<int:user_id>', methods=['POST'])
@admin_required
def edit_employee(user_id):
    name = request.form.get('name', '').strip()
    workplace = request.form.get('workplace', '').strip()
    if not name:
        flash('名前を入力してください', 'error')
        return redirect(url_for('admin_dashboard'))
    db.update_employee(user_id, name, workplace)
    flash(f'{name} の情報を更新しました', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/edit_attendance', methods=['POST'])
@admin_required
def edit_attendance():
    user_id = int(request.form.get('user_id'))
    date = request.form.get('date', '').strip()
    clock_in = request.form.get('clock_in', '').strip()
    clock_out = request.form.get('clock_out', '').strip()
    if not date:
        flash('日付を入力してください', 'error')
        return redirect(url_for('admin_dashboard'))
    db.update_attendance(user_id, date, clock_in, clock_out)
    flash(f'{date} の勤怠データを更新しました', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/download')
@admin_required
def download():
    fmt = request.args.get('format', 'csv')
    year_month = request.args.get('year_month', datetime.now().strftime('%Y-%m'))
    employee_id = request.args.get('employee_id', '')

    workplace = request.args.get('workplace', '')
    user_id = int(employee_id) if employee_id else None
    records = db.get_attendance_records(user_id=user_id, year_month=year_month)

    if workplace:
        records = [r for r in records if r['workplace'] == workplace]

    headers = ['日付', '名前', '勤務先', '出勤', '退勤', '休憩時間', '労働時間']

    if fmt == 'excel':
        wb = Workbook()
        ws = wb.active
        ws.title = '勤怠データ'
        ws.append(headers)

        for r in records:
            ws.append([
                r['date'], r['name'], r['workplace'],
                r['clock_in'] or '', r['clock_out'] or '',
                r['break_time'], r['work_hours']
            ])

        # 列幅の自動調整
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            ws.column_dimensions[col[0].column_letter].width = max_len + 4

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        filename = f'kintai_{year_month}.xlsx'
        return send_file(output, as_attachment=True, download_name=filename,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    else:  # CSV
        import csv
        output = io.StringIO()
        # BOM付きUTF-8（Excelで文字化けしないように）
        output.write('\ufeff')
        writer = csv.writer(output)
        writer.writerow(headers)

        for r in records:
            writer.writerow([
                r['date'], r['name'], r['workplace'],
                r['clock_in'] or '', r['clock_out'] or '',
                r['break_time'], r['work_hours']
            ])

        output.seek(0)
        filename = f'kintai_{year_month}.csv'
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8')),
            as_attachment=True,
            download_name=filename,
            mimetype='text/csv; charset=utf-8'
        )


if __name__ == '__main__':
    db.init_db()
    print('=== 勤怠管理システム ===')
    print('http://localhost:5000 でアクセスしてください')
    print('管理者ログイン → ID: admin / パスワード: admin123')
    print('========================')
    app.run(debug=False, host='0.0.0.0', port=5000)
