import os
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'

import unittest
from app import create_app, db
from app.models import User

class SecurityTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()

        # Re-create database for test
        db.create_all()

        # Seed users
        self.admin = User(name="Admin User", email="admin@example.com", role="admin")
        self.admin.set_password("password")
        
        self.member = User(name="Member User", email="member@example.com", role="member")
        self.member.set_password("password")

        db.session.add(self.admin)
        db.session.add(self.member)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def login(self, email, password):
        return self.client.post('/auth/login', data=dict(
            email=email,
            password=password
        ), follow_redirects=True)

    def logout(self):
        return self.client.post('/auth/logout', follow_redirects=True)

    def test_anonymous_access_blocked(self):
        """Anonymous user should be redirected to login from admin routes"""
        response = self.client.get('/admin/', follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/auth/login', response.location)

        response = self.client.get('/admin/posts', follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/auth/login', response.location)

    def test_member_access_blocked(self):
        """Member (non-admin) should be blocked and redirected to homepage with warning"""
        self.login('member@example.com', 'password')
        
        # Accessing /admin/ should redirect to home page (main.index)
        response = self.client.get('/admin/', follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/index')

        # Attempt to access post management
        response = self.client.get('/admin/posts', follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/index')

        # Verify flash warning in response data after redirect
        response_redirected = self.client.get('/admin/posts', follow_redirects=True)
        self.assertIn('管理画面へのアクセス権限がありません。', response_redirected.data.decode('utf-8'))

    def test_admin_access_allowed(self):
        """Admin user should be allowed to access admin routes"""
        self.login('admin@example.com', 'password')
        
        response = self.client.get('/admin/', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('投稿一覧', response.data.decode('utf-8'))

        response = self.client.get('/admin/posts', follow_redirects=True)
        self.assertEqual(response.status_code, 200)

    def test_logout_helper_clears_authenticated_session(self):
        """The test helper should use the supported logout method and clear the session."""
        self.login('admin@example.com', 'password')

        response = self.logout()

        self.assertEqual(response.status_code, 200)
        protected_response = self.client.get('/admin/posts', follow_redirects=False)
        self.assertEqual(protected_response.status_code, 302)
        self.assertIn('/auth/login', protected_response.location)

if __name__ == '__main__':
    unittest.main()
