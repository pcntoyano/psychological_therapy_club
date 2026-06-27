from flask import render_template, redirect, url_for, flash, request, current_app
from app.blueprints.admin import admin_bp
from flask_login import login_required, current_user
from app.models import Post, Category, User, Comment, db
from datetime import datetime, timedelta
import os
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # Python 3.8
import uuid
from werkzeug.utils import secure_filename

@admin_bp.before_request
def check_admin_required():
    if not current_user.is_authenticated:
        return current_app.login_manager.unauthorized()
    if not current_user.is_admin:
        flash('管理画面へのアクセス権限がありません。', 'error')
        return redirect(url_for('main.index'))

@admin_bp.route('/')
@login_required
def index():
    return redirect(url_for('admin.post_list'))

@admin_bp.route('/posts')
@login_required
def post_list():
    page = request.args.get('page', 1, type=int)
    q = request.args.get('q', '')
    category_id = request.args.get('category', type=int)

    query = Post.query
    if q:
        query = query.filter(Post.title.contains(q))
    if category_id:
        query = query.filter(Post.category_id == category_id)

    pagination = query.order_by(Post.published_at.desc()).paginate(page=page, per_page=10, error_out=False)
    posts = pagination.items
    categories = Category.query.order_by(Category.name).all()

    # 状態の判定は日本時間で行う（published_at はフォームで JST として入力されている想定）
    if ZoneInfo:
        now_jst = datetime.now(ZoneInfo('Asia/Tokyo')).replace(tzinfo=None)
    else:
        now_jst = datetime.utcnow() + timedelta(hours=9)

    return render_template('admin/post_list.html',
                         title='記事管理',
                         posts=posts,
                         pagination=pagination,
                         categories=categories,
                         q=q,
                         category_id=category_id,
                         now=now_jst)

@admin_bp.route('/posts/new', methods=['GET', 'POST'])
@login_required
def create_post():
    categories = Category.query.all()
    users = User.query.all()
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        category_id = request.form.get('category_id')
        user_id = request.form.get('user_id') or current_user.id
        published_at_str = request.form.get('published_at')
        published_at = datetime.strptime(published_at_str, '%Y-%m-%dT%H:%M') if published_at_str else datetime.utcnow()

        event_date_str = request.form.get('event_date')
        event_date = datetime.strptime(event_date_str, '%Y-%m-%dT%H:%M') if event_date_str else None

        # アイキャッチ画像の処理
        eyecatch_img = None
        file = request.files.get('eyecatch_img')
        if file and file.filename != '':
            filename = secure_filename(file.filename)
            ext = os.path.splitext(filename)[1]
            new_filename = f"{uuid.uuid4().hex}{ext}"
            upload_path = os.path.join(current_app.root_path, 'static/uploads/eyecatch')
            if not os.path.exists(upload_path):
                os.makedirs(upload_path)
            file.save(os.path.join(upload_path, new_filename))
            eyecatch_img = new_filename

        # バリデーション
        errors = []
        if not title:
            errors.append('見出しを入力してください。')
        if not content:
            errors.append('本文を入力してください。')
        if not category_id:
            errors.append('カテゴリーを選択してください。')
        if not user_id:
            errors.append('投稿者を選択してください。')
        if not published_at_str:
            errors.append('公開日時を入力してください。')

        # イベントカテゴリの場合、開催日は必須
        category = Category.query.get(category_id)
        if category and category.slug == 'event' and not event_date:
            errors.append('カテゴリーが「イベント」の場合は、イベント開催日を入力してください。')

        if errors:
            for error in errors:
                flash(error, 'error')
            return render_template('admin/post_form.html', title='新規記事投稿', categories=categories, users=users, post=None, values=request.form)

        post = Post(
            title=title,
            content=content,
            category_id=int(category_id),
            user_id=int(user_id),
            published_at=published_at,
            event_date=event_date,
            eyecatch_img=eyecatch_img
        )
        db.session.add(post)
        db.session.commit()
        flash('記事を投稿しました。')
        return redirect(url_for('admin.post_list'))
        
    return render_template('admin/post_form.html', title='新規記事投稿', categories=categories, users=users)

# --- 会員管理 ---

@admin_bp.route('/users')
@login_required
def admin_users():
    page = request.args.get('page', 1, type=int)
    q = request.args.get('q', '')
    role = request.args.get('role', '')
    sort = request.args.get('sort', 'newest')
    
    query = User.query
    if q:
        query = query.filter((User.name.contains(q)) | (User.email.contains(q)))
    
    if role:
        query = query.filter(User.role == role)
    
    if sort == 'name_asc':
        query = query.order_by(User.name.asc())
    elif sort == 'name_desc':
        query = query.order_by(User.name.desc())
    else:
        query = query.order_by(User.created_at.desc())
        
    pagination = query.paginate(page=page, per_page=10, error_out=False)
    users = pagination.items
    
    return render_template('admin/admin_users.html', 
                         users=users, 
                         pagination=pagination, 
                         q=q,
                         role=role,
                         sort=sort,
                         active_menu='user_list')

@admin_bp.route('/users/new', methods=['GET', 'POST'])
@login_required
def admin_user_new():
    if request.method == 'POST':
        name = request.form.get('name')
        display_name = (request.form.get('display_name') or '').strip() or None
        email = request.form.get('email')
        password = request.form.get('password')
        password_confirm = request.form.get('password_confirm')
        role = request.form.get('role', 'member')
        profile = request.form.get('bio')
        
        errors = {}
        if not name:
            errors['name'] = '名前を入力してください。'
        if not email:
            errors['email'] = 'メールアドレスを入力してください。'
        elif User.query.filter_by(email=email).first():
            errors['email'] = 'このメールアドレスは既に登録されています。'
        
        if not password:
            errors['password'] = 'パスワードを入力してください。'
        elif password != password_confirm:
            errors['password_confirm'] = 'パスワードが一致しません。'
            
        if errors:
            return render_template('admin/admin_user_new.html', 
                                 active_menu='user_new',
                                 errors=errors,
                                 values=request.form)
        
        user = User(name=name, display_name=display_name, email=email, role=role, profile=profile)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        
        flash(f'会員「{name}」を登録しました。')
        return redirect(url_for('admin.admin_users'))
        
    return render_template('admin/admin_user_new.html', active_menu='user_new')

@admin_bp.route('/users/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def admin_user_edit(id):
    user = User.query.get_or_404(id)
    if request.method == 'POST':
        name = request.form.get('name')
        display_name = (request.form.get('display_name') or '').strip() or None
        email = request.form.get('email')
        password = request.form.get('password')
        password_confirm = request.form.get('password_confirm')
        role = request.form.get('role', 'member')
        profile = request.form.get('bio')
        
        errors = {}
        if not name:
            errors['name'] = '名前を入力してください。'
        if not email:
            errors['email'] = 'メールアドレスを入力してください。'
        else:
            existing_user = User.query.filter_by(email=email).first()
            if existing_user and existing_user.id != user.id:
                errors['email'] = 'このメールアドレスは既に他のユーザーに使用されています。'
        
        if password:
            if password != password_confirm:
                errors['password_confirm'] = 'パスワードが一致しません。'
            
        if errors:
            return render_template('admin/admin_user_edit.html', 
                                 active_menu='user_list',
                                 user=user,
                                 errors=errors,
                                 values=request.form)
        
        user.name = name
        user.display_name = display_name
        user.email = email
        user.role = role
        user.profile = profile
        if password:
            user.set_password(password)
        
        db.session.commit()
        flash(f'会員「{name}」の情報を更新しました。')
        return redirect(url_for('admin.admin_users'))
        
    return render_template('admin/admin_user_edit.html', user=user, active_menu='user_list')

# --- 記事編集・削除 ---

@admin_bp.route('/posts/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_post(id):
    post = Post.query.get_or_404(id)
    categories = Category.query.all()
    users = User.query.all()
    if request.method == 'POST':
        post.title = request.form.get('title')
        post.content = request.form.get('content')
        post.category_id = request.form.get('category_id')
        
        user_id = request.form.get('user_id')
        if user_id:
            post.user_id = user_id
        
        published_at_str = request.form.get('published_at')
        if published_at_str:
            post.published_at = datetime.strptime(published_at_str, '%Y-%m-%dT%H:%M')
            
        event_date_str = request.form.get('event_date')
        if event_date_str:
            post.event_date = datetime.strptime(event_date_str, '%Y-%m-%dT%H:%M')
        else:
            post.event_date = None
        
        # アイキャッチ画像の処理
        file = request.files.get('eyecatch_img')
        if file and file.filename != '':
            filename = secure_filename(file.filename)
            ext = os.path.splitext(filename)[1]
            new_filename = f"{uuid.uuid4().hex}{ext}"
            upload_path = os.path.join(current_app.root_path, 'static/uploads/eyecatch')
            if not os.path.exists(upload_path):
                os.makedirs(upload_path)
            file.save(os.path.join(upload_path, new_filename))
            
            # 古い画像があれば削除（オプションですが、今回はシンプルに上書き扱いでカラム更新のみ）
            post.eyecatch_img = new_filename
        
        # バリデーション
        errors = []
        if not post.title:
            errors.append('見出しを入力してください。')
        if not post.content:
            errors.append('本文を入力してください。')
        if not post.category_id:
            errors.append('カテゴリーを選択してください。')
        
        # イベントカテゴリの場合、開催日は必須
        category = Category.query.get(post.category_id)
        if category and category.slug == 'event' and not post.event_date:
            errors.append('カテゴリーが「イベント」の場合は、イベント開催日を入力してください。')
            
        if errors:
            for error in errors:
                flash(error, 'error')
            return render_template('admin/post_form.html', title='記事編集', post=post, categories=categories, users=users)

        db.session.commit()
        flash('記事を更新しました。')
        return redirect(url_for('admin.post_list'))
        
    return render_template('admin/post_form.html', title='記事編集', post=post, categories=categories, users=users)

@admin_bp.route('/posts/<int:id>/delete', methods=['POST'])
@login_required
def delete_post(id):
    post = Post.query.get_or_404(id)
    db.session.delete(post)
    db.session.commit()
    flash('記事を削除しました。')
    return redirect(url_for('admin.post_list'))


@admin_bp.route('/posts/<int:id>/comments')
@login_required
def post_comment_list(id):
    post = Post.query.get_or_404(id)
    comments = Comment.query.filter_by(post_id=post.id).order_by(Comment.created_at.desc()).all()
    return render_template('admin/post_comment_list.html', post=post, comments=comments)


@admin_bp.route('/posts/<int:post_id>/comments/<int:comment_id>/delete', methods=['POST'])
@login_required
def delete_comment(post_id, comment_id):
    comment = Comment.query.filter_by(id=comment_id, post_id=post_id).first_or_404()
    db.session.delete(comment)
    db.session.commit()
    flash('コメントを削除しました。')
    return redirect(url_for('admin.post_comment_list', id=post_id))
