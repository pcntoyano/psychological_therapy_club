from flask import render_template, request, url_for, redirect, flash
from app.blueprints.main import main_bp
from app.models import Post, Category, Comment, db
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


def _now_jst():
    """日本時間の現在時刻（naive、published_at と比較用）"""
    if ZoneInfo:
        return datetime.now(ZoneInfo('Asia/Tokyo')).replace(tzinfo=None)
    return datetime.utcnow() + timedelta(hours=9)


@main_bp.route('/')
@main_bp.route('/index')
def index():
    now_jst = _now_jst()
    # Topics (Category: topics)
    topics_category = Category.query.filter_by(slug='topics').first()
    topics_posts = []
    if topics_category:
        topics_posts = Post.query.filter(
            Post.category_id == topics_category.id,
            Post.published_at <= now_jst
        ).order_by(Post.published_at.desc()).limit(5).all()

    # Events (Category: event)
    event_category = Category.query.filter_by(slug='event').first()
    event_posts = []
    if event_category:
        event_posts = Post.query.filter(
            Post.category_id == event_category.id,
            Post.published_at <= now_jst
        ).order_by(Post.published_at.desc()).limit(4).all()

    return render_template('main/index.html', title='ホーム', 
                         posts=topics_posts, # Backward compatibility for base.html if needed, or primarily for topics section
                         topics_posts=topics_posts,
                         event_posts=event_posts)

@main_bp.route('/blog')
def blog_list():
    page = request.args.get('page', 1, type=int)
    category_slug = request.args.get('category')
    now_jst = _now_jst()
    query = Post.query.filter(Post.published_at <= now_jst)
    current_category = None
    
    if category_slug:
        current_category = Category.query.filter_by(slug=category_slug).first()
        if current_category:
            query = query.filter_by(category_id=current_category.id)
            
    pagination = query.order_by(Post.published_at.desc()).paginate(page=page, per_page=10, error_out=False)
    posts = pagination.items
    categories = Category.query.order_by(Category.name).all()
    # サイドバー用：カテゴリーごとの公開済み投稿数
    category_counts = {c.id: Post.query.filter(Post.category_id == c.id, Post.published_at <= now_jst).count() for c in categories}
    total_published = Post.query.filter(Post.published_at <= now_jst).count()

    return render_template('main/blog_list.html',
                         title='ブログ記事一覧',
                         posts=posts,
                         pagination=pagination,
                         categories=categories,
                         current_category=current_category,
                         category_counts=category_counts,
                         total_published=total_published)

@main_bp.route('/blog/detail/<int:id>')
def blog_detail(id):
    now_jst = _now_jst()
    post = Post.query.filter(
        Post.id == id,
        Post.published_at <= now_jst,
    ).first_or_404()
    base_query = Post.query.filter(Post.published_at <= now_jst)

    # 前の記事（より新しい公開記事）
    prev_post = base_query.filter(Post.published_at > post.published_at).order_by(Post.published_at.asc()).first()
    # 次の記事（より古い公開記事）
    next_post = base_query.filter(Post.published_at < post.published_at).order_by(Post.published_at.desc()).first()

    categories = Category.query.order_by(Category.name).all()
    category_counts = {c.id: base_query.filter(Post.category_id == c.id).count() for c in categories}
    recent_posts = base_query.filter(Post.id != post.id).order_by(Post.published_at.desc()).limit(5).all()

    return render_template('main/blog_detail.html',
                         post=post,
                         categories=categories,
                         category_counts=category_counts,
                         recent_posts=recent_posts,
                         prev_post=prev_post,
                         next_post=next_post)


@main_bp.route('/blog/detail/<int:id>/comment', methods=['POST'])
def post_comment(id):
    post = Post.query.filter(
        Post.id == id,
        Post.published_at <= _now_jst(),
    ).first_or_404()
    name = (request.form.get('name') or '').strip()
    content = (request.form.get('content') or '').strip()

    errors = []
    if not name:
        errors.append('名前を入力してください。')
    if not content:
        errors.append('コメントを入力してください。')

    if errors:
        for msg in errors:
            flash(msg, 'error')
        return redirect(url_for('main.blog_detail', id=post.id))

    comment = Comment(post_id=post.id, user_id=None, name=name, content=content)
    db.session.add(comment)
    db.session.commit()
    flash('コメントを投稿しました。')
    return redirect(url_for('main.blog_detail', id=post.id))


@main_bp.route('/essay')
def essay_list():
    return redirect(url_for('main.blog_list', category='essay'))

@main_bp.route('/event')
def event_list():
    return redirect(url_for('main.blog_list', category='event'))
