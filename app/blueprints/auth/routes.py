from flask import render_template, redirect, url_for, flash, request
from app.blueprints.auth import auth_bp
from flask_login import login_user, logout_user, current_user
from app.models import User, db
from urllib.parse import urlparse

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form.get('email')).first()
        if user is None or not user.check_password(request.form.get('password')):
            flash('メールアドレスまたはパスワードが正しくありません。')
            return redirect(url_for('auth.login'))
        
        remember = request.form.get('remember') == 'on'
        login_user(user, remember=remember)
        next_page = request.args.get('next')
        if not next_page or urlparse(next_page).netloc != '':
            if user.is_admin:
                next_page = url_for('admin.post_list')
            else:
                next_page = url_for('main.index')
        else:
            if not user.is_admin and next_page.startswith('/admin'):
                next_page = url_for('main.index')
        return redirect(next_page)
        
    return render_template('auth/admin_login.html', title='ログイン')

@auth_bp.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('auth.login'))

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        display_name = request.form.get('display_name', '').strip() or None
        email = request.form.get('email', '').strip()
        password = request.form.get('password')
        password_confirm = request.form.get('password_confirm')
        
        errors = []
        if not name:
            errors.append('名前を入力してください。')
        if not email:
            errors.append('メールアドレスを入力してください。')
        elif User.query.filter_by(email=email).first():
            errors.append('このメールアドレスは既に登録されています。')
        
        if not password:
            errors.append('パスワードを入力してください。')
        elif password != password_confirm:
            errors.append('パスワードが一致しません。')
            
        if errors:
            for error in errors:
                flash(error, 'error')
            return render_template('auth/register.html', title='新規会員登録')
        
        user = User(name=name, display_name=display_name, email=email, role='member')
        user.set_password(password)
        
        try:
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash('会員登録が完了しました。ログインしました。')
            return redirect(url_for('main.index'))
        except Exception as e:
            db.session.rollback()
            flash('会員登録処理中にエラーが発生しました。時間を置いて再度お試しください。', 'error')
            return render_template('auth/register.html', title='新規会員登録')
            
    return render_template('auth/register.html', title='新規会員登録')

