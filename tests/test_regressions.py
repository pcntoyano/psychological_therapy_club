import os
import re
import tempfile
import unittest
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from flask import session
from werkzeug.datastructures import FileStorage


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-only-secret"

from app import create_app, db
from app.blueprints.main.routes import _now_jst
from app.models import Category, Comment, Post, User


class DatabaseTestCase(unittest.TestCase):
    csrf_enabled = False

    def setUp(self):
        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            PROPAGATE_EXCEPTIONS=False,
            WTF_CSRF_ENABLED=self.csrf_enabled,
        )
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        self.admin = User(name="Admin", email="admin@example.com", role="admin")
        self.admin.set_password("password")
        self.category = Category(name="Topics", slug="topics")
        db.session.add_all([self.admin, self.category])
        db.session.commit()

        self.post = Post(
            user_id=self.admin.id,
            category_id=self.category.id,
            title="Published post",
            content="Body",
            published_at=_now_jst() - timedelta(days=1),
        )
        db.session.add(self.post)
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def authenticate_admin_session(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True


class CsrfProtectionTestCase(DatabaseTestCase):
    csrf_enabled = True

    def test_tokenless_admin_delete_is_rejected(self):
        self.authenticate_admin_session()

        response = self.client.post(
            f"/admin/posts/{self.post.id}/delete",
            headers={"Origin": "https://attacker.example"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIsNotNone(db.session.get(Post, self.post.id))

    def test_tokenless_public_comment_is_rejected(self):
        response = self.client.post(
            f"/blog/detail/{self.post.id}/comment",
            data={"name": "attacker", "content": "cross-site submission"},
            headers={"Origin": "https://attacker.example"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Comment.query.count(), 0)

    def test_logout_is_not_available_over_get(self):
        self.authenticate_admin_session()

        response = self.client.get("/auth/logout")

        self.assertEqual(response.status_code, 405)

    def test_csrf_token_allows_normal_logout(self):
        self.authenticate_admin_session()
        page = self.client.get("/admin/posts")
        token_match = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            page.data,
        )
        self.assertIsNotNone(token_match)

        response = self.client.post(
            "/auth/logout",
            data={"csrf_token": token_match.group(1).decode()},
        )

        self.assertEqual(response.status_code, 302)
        protected_page = self.client.get("/admin/posts")
        self.assertTrue(protected_page.location.endswith("/auth/login?next=%2Fadmin%2Fposts"))


class PublicationVisibilityTestCase(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.future_post = Post(
            user_id=self.admin.id,
            category_id=self.category.id,
            title="Future post",
            content="Not published yet",
            published_at=_now_jst() + timedelta(days=1),
        )
        db.session.add(self.future_post)
        db.session.commit()

    def test_future_post_is_not_publicly_readable_by_id(self):
        response = self.client.get(f"/blog/detail/{self.future_post.id}")

        self.assertEqual(response.status_code, 404)

    def test_future_post_does_not_accept_public_comments(self):
        response = self.client.post(
            f"/blog/detail/{self.future_post.id}/comment",
            data={"name": "Visitor", "content": "Premature comment"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            Comment.query.filter_by(post_id=self.future_post.id).count(),
            0,
        )


class EyecatchUploadTestCase(DatabaseTestCase):
    PNG_BYTES = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01"
    )

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.app.root_path = self.temp_dir.name
        self.upload_dir = Path(self.app.root_path) / "static/uploads/eyecatch"
        self.upload_dir.mkdir(parents=True)
        self.authenticate_admin_session()

    def post_with_image(self, title, contents, filename):
        return self.client.post(
            "/admin/posts/new",
            data={
                "title": title,
                "content": "Body",
                "category_id": str(self.category.id),
                "user_id": str(self.admin.id),
                "published_at": "2026-07-22T12:00",
                "eyecatch_img": (BytesIO(contents), filename),
            },
            content_type="multipart/form-data",
        )

    def test_active_content_upload_is_rejected(self):
        response = self.post_with_image(
            "Unsafe image",
            b"<script>window.__upload_marker = true</script>",
            "payload.html",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Post.query.count(), 1)
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    def test_spoofed_image_extension_is_rejected(self):
        response = self.post_with_image(
            "Spoofed image",
            b"<script>window.__upload_marker = true</script>",
            "payload.png",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Post.query.count(), 1)
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    def test_validation_error_does_not_leave_orphaned_image(self):
        response = self.post_with_image("", self.PNG_BYTES, "valid.png")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Post.query.count(), 1)
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    def test_save_failure_removes_partially_written_image(self):
        def fail_after_partial_write(file_storage, destination, buffer_size=16384):
            Path(destination).write_bytes(b"partial image")
            raise OSError("forced write failure")

        with patch.object(FileStorage, "save", fail_after_partial_write):
            response = self.post_with_image(
                "Write failure",
                self.PNG_BYTES,
                "valid.png",
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(Post.query.filter_by(title="Write failure").count(), 0)
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    def test_valid_image_is_saved_with_safe_extension(self):
        response = self.post_with_image(
            "Valid image",
            self.PNG_BYTES,
            "valid.png",
        )

        self.assertEqual(response.status_code, 302)
        post = Post.query.filter_by(title="Valid image").one()
        self.assertTrue(post.eyecatch_img.endswith(".png"))
        self.assertTrue((self.upload_dir / post.eyecatch_img).is_file())

    def test_create_commit_failure_rolls_back_and_removes_new_image(self):
        with (
            patch.object(
                db.session,
                "commit",
                side_effect=RuntimeError("forced commit failure"),
            ),
            patch.object(db.session, "rollback", wraps=db.session.rollback) as rollback,
        ):
            response = self.post_with_image(
                "Commit failure",
                self.PNG_BYTES,
                "valid.png",
            )

        self.assertEqual(response.status_code, 500)
        rollback.assert_called_once_with()
        self.assertEqual(Post.query.filter_by(title="Commit failure").count(), 0)
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    def test_edit_commit_failure_rolls_back_and_removes_new_image(self):
        with (
            patch.object(
                db.session,
                "commit",
                side_effect=RuntimeError("forced commit failure"),
            ),
            patch.object(db.session, "rollback", wraps=db.session.rollback) as rollback,
        ):
            response = self.client.post(
                f"/admin/posts/{self.post.id}/edit",
                data={
                    "title": "Attempted update",
                    "content": "Changed body",
                    "category_id": str(self.category.id),
                    "user_id": str(self.admin.id),
                    "published_at": "2026-07-22T12:00",
                    "eyecatch_img": (BytesIO(self.PNG_BYTES), "valid.png"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 500)
        rollback.assert_called_once_with()
        db.session.expire_all()
        self.assertEqual(db.session.get(Post, self.post.id).title, "Published post")
        self.assertEqual(list(self.upload_dir.iterdir()), [])


class InputValidationTestCase(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.authenticate_admin_session()

    def valid_post_data(self, **overrides):
        data = {
            "title": "New post",
            "content": "Body",
            "category_id": str(self.category.id),
            "user_id": str(self.admin.id),
            "published_at": "2026-07-22T12:00",
        }
        data.update(overrides)
        return data

    def test_login_with_missing_password_fails_normally(self):
        anonymous_client = self.app.test_client()

        response = anonymous_client.post(
            "/auth/login",
            data={"email": self.admin.email},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/auth/login"))

    def test_malformed_post_datetime_is_validation_error(self):
        response = self.client.post(
            "/admin/posts/new",
            data=self.valid_post_data(
                title="Preserved draft title",
                content="Preserved draft body",
                published_at="not-a-date",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Post.query.count(), 1)
        self.assertIn(b"Preserved draft title", response.data)
        self.assertIn(b"Preserved draft body", response.data)

    def test_unknown_category_is_validation_error(self):
        response = self.client.post(
            "/admin/posts/new",
            data=self.valid_post_data(category_id="99999"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Post.query.count(), 1)

    def test_unknown_author_is_validation_error(self):
        response = self.client.post(
            "/admin/posts/new",
            data=self.valid_post_data(user_id="99999"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Post.query.count(), 1)

    def test_malformed_edit_datetime_does_not_change_post(self):
        response = self.client.post(
            f"/admin/posts/{self.post.id}/edit",
            data=self.valid_post_data(
                title="Attempted update",
                published_at="not-a-date",
            ),
        )

        self.assertEqual(response.status_code, 200)
        db.session.expire_all()
        self.assertEqual(db.session.get(Post, self.post.id).title, "Published post")


class LegacyRouteTestCase(DatabaseTestCase):
    def test_legacy_content_routes_redirect_to_working_lists(self):
        expected_locations = {
            "/essay": "/blog?category=essay",
            "/event": "/blog?category=event",
            "/blog/detail": "/blog",
        }

        for path, expected_location in expected_locations.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.location.endswith(expected_location))


class FlaskDependencySecurityTestCase(DatabaseTestCase):
    def setUp(self):
        super().setUp()

        @self.app.get("/_test/session-membership")
        def session_membership():
            return {"remembered": "_remember" in session}

    def test_session_membership_access_sets_vary_cookie(self):
        response = self.client.get("/_test/session-membership")

        vary_values = response.headers.getlist("Vary")
        self.assertIn("Cookie", ", ".join(vary_values))


if __name__ == "__main__":
    unittest.main()
