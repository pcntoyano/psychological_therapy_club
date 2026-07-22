from flask import render_template, redirect, url_for, flash, request, current_app
from app.blueprints.admin import admin_bp
from flask_login import login_required, current_user
from app.models import Post, Category, User, Comment, db
from datetime import datetime, timedelta
import os
from types import SimpleNamespace
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # Python 3.8
import uuid
from werkzeug.utils import secure_filename


def _validated_image_extension(file):
    filename = secure_filename(file.filename or '')
    requested_extension = os.path.splitext(filename)[1].lower()

    header = file.stream.read(16)
    file.stream.seek(0)

    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        detected_extension = '.png'
    elif header.startswith(b'\xff\xd8\xff'):
        detected_extension = '.jpg'
    elif header.startswith((b'GIF87a', b'GIF89a')):
        detected_extension = '.gif'
    elif header.startswith(b'RIFF') and header[8:12] == b'WEBP':
        detected_extension = '.webp'
    else:
        return None

    if detected_extension == '.jpg':
        return detected_extension if requested_extension in {'.jpg', '.jpeg'} else None
    return detected_extension if requested_extension == detected_extension else None


def _save_eyecatch(file, extension):
    new_filename = f"{uuid.uuid4().hex}{extension}"
    temporary_filename = f".{new_filename}.tmp"
    upload_path = os.path.join(current_app.root_path, 'static/uploads/eyecatch')
    os.makedirs(upload_path, exist_ok=True)
    temporary_path = os.path.join(upload_path, temporary_filename)
    final_path = os.path.join(upload_path, new_filename)
    try:
        file.save(temporary_path)
        os.replace(temporary_path, final_path)
    except Exception:
        _remove_eyecatch(temporary_filename)
        raise
    return new_filename


def _remove_eyecatch(filename):
    if not filename:
        return

    path = os.path.join(
        current_app.root_path,
        'static/uploads/eyecatch',
        filename,
    )
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        current_app.logger.exception(
            'Failed to remove uncommitted eyecatch image %s',
            filename,
        )


def _parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime_local(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M')
    except ValueError:
        return None


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
        category_id = _parse_int(request.form.get('category_id'))
        requested_user_id = request.form.get('user_id')
        user_id = _parse_int(requested_user_id) if requested_user_id else current_user.id
        published_at_str = request.form.get('published_at')
        published_at = _parse_datetime_local(published_at_str)
        event_date_str = request.form.get('event_date')
        event_date = _parse_datetime_local(event_date_str)

        errors = []
        category = db.session.get(Category, category_id) if category_id is not None else None
        author = db.session.get(User, user_id) if user_id is not None else None

        file = request.files.get('eyecatch_img')
        image_extension = None
        if file and file.filename != '':
            image_extension = _validated_image_extension(file)
            if image_extension is None:
                errors.append('画像は内容と拡張子が一致する PNG、JPG、GIF、WEBP 形式を選択してください。')

        # バリデーション
        if not title:
            errors.append('見出しを入力してください。')
        if not content:
            errors.append('本文を入力してください。')
        if category_id is None:
            errors.append('カテゴリーを選択してください。')
        elif category is None:
            errors.append('選択されたカテゴリーが見つかりません。')
        if user_id is None:
            errors.append('投稿者を選択してください。')
        elif author is None:
            errors.append('選択された投稿者が見つかりません。')
        if not published_at_str:
            errors.append('公開日時を入力してください。')
        elif published_at is None:
            errors.append('公開日時の形式が正しくありません。')
        if event_date_str and event_date is None:
            errors.append('イベント開催日時の形式が正しくありません。')

        # イベントカテゴリの場合、開催日は必須
        if category and category.slug == 'event' and not event_date:
            errors.append('カテゴリーが「イベント」の場合は、イベント開催日を入力してください。')

        if errors:
            for error in errors:
                flash(error, 'error')
            return render_template('admin/post_form.html', title='新規記事投稿', categories=categories, users=users, post=None, values=request.form)

        eyecatch_img = _save_eyecatch(file, image_extension) if image_extension else None
        post = Post(
            title=title,
            content=content,
            category_id=category_id,
            user_id=user_id,
            published_at=published_at,
            event_date=event_date,
            eyecatch_img=eyecatch_img
        )
        db.session.add(post)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            _remove_eyecatch(eyecatch_img)
            raise
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
        title = (request.form.get('title') or '').strip()
        content = (request.form.get('content') or '').strip()
        category_id = _parse_int(request.form.get('category_id'))
        user_id = _parse_int(request.form.get('user_id'))
        published_at_str = request.form.get('published_at')
        published_at = _parse_datetime_local(published_at_str)
        event_date_str = request.form.get('event_date')
        event_date = _parse_datetime_local(event_date_str)

        errors = []
        category = db.session.get(Category, category_id) if category_id is not None else None
        author = db.session.get(User, user_id) if user_id is not None else None

        file = request.files.get('eyecatch_img')
        image_extension = None
        if file and file.filename != '':
            image_extension = _validated_image_extension(file)
            if image_extension is None:
                errors.append('画像は内容と拡張子が一致する PNG、JPG、GIF、WEBP 形式を選択してください。')
        
        # バリデーション
        if not title:
            errors.append('見出しを入力してください。')
        if not content:
            errors.append('本文を入力してください。')
        if category_id is None:
            errors.append('カテゴリーを選択してください。')
        elif category is None:
            errors.append('選択されたカテゴリーが見つかりません。')
        if user_id is None:
            errors.append('投稿者を選択してください。')
        elif author is None:
            errors.append('選択された投稿者が見つかりません。')
        if not published_at_str:
            errors.append('公開日時を入力してください。')
        elif published_at is None:
            errors.append('公開日時の形式が正しくありません。')
        if event_date_str and event_date is None:
            errors.append('イベント開催日時の形式が正しくありません。')

        # イベントカテゴリの場合、開催日は必須
        if category and category.slug == 'event' and not event_date:
            errors.append('カテゴリーが「イベント」の場合は、イベント開催日を入力してください。')
            
        if errors:
            for error in errors:
                flash(error, 'error')
            submitted_post = SimpleNamespace(
                title=title,
                content=content,
                category_id=category_id,
                user_id=user_id,
                published_at=published_at,
                event_date=event_date,
                eyecatch_img=post.eyecatch_img,
            )
            return render_template('admin/post_form.html', title='記事編集', post=submitted_post, categories=categories, users=users)

        post.title = title
        post.content = content
        post.category_id = category_id
        post.user_id = user_id
        post.published_at = published_at
        post.event_date = event_date
        new_eyecatch_img = _save_eyecatch(file, image_extension) if image_extension else None
        if new_eyecatch_img:
            post.eyecatch_img = new_eyecatch_img
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            _remove_eyecatch(new_eyecatch_img)
            raise
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
