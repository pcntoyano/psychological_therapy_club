from flask import Flask, render_template
from .models import db, User
from flask_login import LoginManager
from flask_migrate import Migrate

migrate = Migrate()
def create_app():
    app = Flask(__name__)
    from config import Config
    app.config.from_object(Config)

    db.init_app(app)
    migrate.init_app(app, db)
    
    login_manager = LoginManager()
    login_manager.login_view = 'auth.login'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Blueprint 登録
    from app.blueprints.main import main_bp
    app.register_blueprint(main_bp)

    from app.blueprints.auth import auth_bp
    app.register_blueprint(auth_bp, url_prefix='/auth')

    from app.blueprints.admin import admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # 仮のトップレベルルート（必要に応じて Blueprint に集約）
    @app.route('/blog/detail')
    def blog_detail():
        return render_template('blog_detail.html')

    return app
