import unittest
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import create_app
from mailer import Mailer


PRODUCT = {
    "id": 1,
    "name": "GP Essential Tee",
    "slug": "gp-essential-tee",
    "description": "Everyday heavyweight cotton tee.",
    "category": "T-Shirts",
    "price": Decimal("699.00"),
    "image_url": "/static/img/poster-graphic-tee.png",
    "stock": 10,
    "featured": True,
}


class MemoryCursor:
    def __init__(self, database):
        self.database = database

    def execute(self, sql, params=()):
        self.database.execute(sql, params)

    def close(self):
        pass


class MemoryConnection:
    def __init__(self, database):
        self.database = database

    def cursor(self):
        return MemoryCursor(self.database)


class MemoryMailer:
    configured = True

    def __init__(self):
        self.messages = []
        self.send_success = True

    def send(self, recipient, subject, body):
        self.messages.append((recipient, subject, body))
        return self.send_success


class MemoryDatabase:
    def __init__(self):
        self.queries = []
        self.executions = []
        self.otp_verifications = {}
        self.created_users = {}
        self.user = {
            "id": 7,
            "name": "Gameplan Customer",
            "email": "customer@example.com",
            "password_hash": generate_password_hash("secure-password-123"),
            "role": "customer",
        }
        self.order = {
            "id": 1,
            "user_id": 7,
            "full_name": "Gameplan Customer",
            "phone": "+639171234567",
            "email": "customer@example.com",
            "address": "123 Street",
            "barangay": "Barangay 123",
            "city": "Manila",
            "province": "Metro Manila",
            "postal_code": "1000",
            "payment_method": "cod",
            "status": "pending",
            "subtotal": Decimal("699.00"),
            "shipping_fee": Decimal("100.00"),
            "total": Decimal("799.00"),
            "created_at": datetime(2026, 1, 1, 0, 0, 0),
            "customer_name": "Gameplan Customer",
        }

    def query(self, sql, params=(), one=False):
        self.queries.append((sql, params))
        if "FROM otp_verifications" in sql:
            row = self.otp_verifications.get(params[0])
            return row.copy() if one and row else None
        if "DISTINCT category" in sql:
            return []
        if "FROM products" in sql:
            if "WHERE id = %s" in sql:
                return PRODUCT.copy() if one and params == (1,) else None
            return [PRODUCT.copy()]
        if "FROM users" in sql:
            if one and params in (("customer@example.com",), (7,), ("7",)):
                return self.user.copy()
            if one and params and params[0] in self.created_users:
                return self.created_users[params[0]].copy()
            return None
        if "FROM orders" in sql:
            if "WHERE o.id = %s" in sql or "WHERE id = %s" in sql:
                return self.order.copy() if one and params == (1,) else None
            return [self.order.copy()]
        if "FROM order_items" in sql:
            return [{
                "product_name": "GP Essential Tee",
                "size": "M",
                "unit_price": Decimal("699.00"),
                "quantity": 1,
            }]
        return [] if not one else None

    def execute(self, sql, params=()):
        self.executions.append((sql, params))
        if "INSERT INTO otp_verifications" in sql:
            email, name, password_hash, role, otp_hash, expires_at, created_at = params
            self.otp_verifications[email] = {
                "email": email,
                "name": name,
                "password_hash": password_hash,
                "role": role,
                "otp_hash": otp_hash,
                "expires_at": expires_at,
                "created_at": created_at,
                "attempts": 0,
            }
        elif "UPDATE otp_verifications SET attempts = attempts + 1" in sql:
            self.otp_verifications[params[0]]["attempts"] += 1
        elif "UPDATE otp_verifications" in sql:
            otp_hash, expires_at, created_at, email = params
            record = self.otp_verifications[email]
            record.update(
                otp_hash=otp_hash,
                expires_at=expires_at,
                attempts=0,
                created_at=created_at,
            )
        elif "DELETE FROM otp_verifications" in sql:
            self.otp_verifications.pop(params[0], None)
        elif "INSERT INTO users" in sql:
            name, email, password_hash, role = params
            self.created_users[email] = {
                "id": 8,
                "name": name,
                "email": email,
                "password_hash": password_hash,
                "role": role,
            }
        return 1, 1

    @contextmanager
    def transaction(self):
        yield MemoryConnection(self)


class StoreAppTests(unittest.TestCase):
    def setUp(self):
        self.database = MemoryDatabase()
        self.mailer = MemoryMailer()
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "WTF_CSRF_ENABLED": False,
                "DATABASE": self.database,
                "MAILER": self.mailer,
            }
        )
        self.client = self.app.test_client()

    def test_home_displays_collections_without_product_listings(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Shop by Category", response.data)
        self.assertIn(b"home-services", response.data)
        self.assertIn(b"MAKE YOUR", response.data)
        self.assertIn(b"OWN GAMEPLAN", response.data)
        self.assertNotIn(b"Watch Lookbook", response.data)
        self.assertNotIn(b"img/blue-logo-tee.png", response.data)
        self.assertIn(b"View All Categories", response.data)
        self.assertIn(b'href="/collections"', response.data)
        self.assertIn(b'href="/shop?category=T-Shirts"', response.data)
        self.assertIn(b'href="/shop?category=Hoodies"', response.data)
        self.assertIn(b'href="/shop?category=Pants"', response.data)
        self.assertIn(b'href="/shop?category=Accessories"', response.data)
        self.assertIn(b"T-Shirts", response.data)
        self.assertIn(b"Hoodies", response.data)
        self.assertIn(b"Pants", response.data)
        self.assertIn(b"Accessories", response.data)
        self.assertIn(b"NEW", response.data)
        self.assertIn(b"ARRIVALS", response.data)
        self.assertNotIn(b"New Drops", response.data)
        self.assertNotIn(b"GP Essential Tee", response.data)
        self.assertNotIn(b"Welcome back", response.data)

    def test_home_is_a_public_landing_page_with_account_actions(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"MAKE YOUR", response.data)
        self.assertIn(b"OWN GAMEPLAN", response.data)
        self.assertIn(b"Shop by Category", response.data)
        self.assertIn(b' href="/register"', response.data)
        self.assertIn(b' href="/login"', response.data)
        self.assertNotIn(b"Watch Lookbook", response.data)
        self.assertNotIn(b"GP Essential Tee", response.data)
        self.assertNotIn(b"Welcome Back!", response.data)

    def test_about_page_shows_brand_story_and_is_public(self):
        response = self.client.get("/about")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"ABOUT US", response.data)
        self.assertIn(b"Our Mission", response.data)
        self.assertIn(b"Our Vision", response.data)
        self.assertIn(b"Our Values", response.data)
        self.assertIn(b'href="/about" class="active"', response.data)
        self.assertNotIn(b"What We Offer", response.data)
        self.assertIn(b"comfortable, stylish, and high-quality everyday wear", response.data)
        self.assertIn("Quality · Style · Comfort".encode(), response.data)

    def test_collections_page_links_each_category_to_filtered_shop(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        response = self.client.get("/collections")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"COLLECTION", response.data)
        self.assertIn(b"All Collection", response.data)
        self.assertIn(b'href="/collections" class="active"', response.data)
        self.assertIn(b'href="/collections?category=T-Shirts"', response.data)
        self.assertIn(b'href="/collections?category=Hoodies"', response.data)
        self.assertIn(b'href="/collections?category=Pants"', response.data)
        self.assertIn(b'href="/collections?category=Accessories"', response.data)
        self.assertIn(b"GP Essential Tee", response.data)
        self.assertIn(b"collection-product-grid", response.data)
        self.assertIn(b'name="min_price"', response.data)
        self.assertIn(b'name="max_price"', response.data)

    def test_collection_size_filter_only_shows_sized_tees(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        response = self.client.get("/collections?size=M")

        self.assertEqual(response.status_code, 200)
        query, params = next(
            (query, params)
            for query, params in self.database.queries
            if "category = %s" in query and "FROM products" in query
        )
        self.assertIn("T-Shirts", params)

    def test_lookbook_page_has_video_placeholders_and_is_public(self):
        response = self.client.get("/lookbook")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"THE LOOKBOOK", response.data)
        self.assertIn(b"Gameplan in Motion", response.data)
        self.assertEqual(response.data.count(b"<video controls"), 3)
        self.assertIn(b'href="/shop"', response.data)

    def test_login_from_public_landing_page_redirects_to_home(self):
        login = self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        self.assertEqual(login.status_code, 302)
        self.assertEqual(login.headers["Location"], "/")

    def test_guidelines_page_is_removed(self):
        response = self.client.get("/guidelines")
        self.assertEqual(response.status_code, 404)

    def test_catalog_and_cart_require_sign_in(self):
        for path in ("/shop", "/products/1", "/cart"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.headers["Location"].startswith("/login?next="))

    def test_app_factory_does_not_connect_to_mysql_until_a_request(self):
        with patch("app.Database.initialize") as initialize:
            create_app(
                {
                    "TESTING": True,
                    "SECRET_KEY": "test-key",
                    "WTF_CSRF_ENABLED": False,
                }
            )
        initialize.assert_not_called()

    def test_customer_auth_pages_render_their_account_forms(self):
        login = self.client.get("/login")
        register = self.client.get("/register")

        self.assertEqual(login.status_code, 200)
        self.assertIn(b'name="email"', login.data)
        self.assertIn(b"Welcome Back!", login.data)
        self.assertIn(b"Remember Me", login.data)
        self.assertIn(b"Sign Up", login.data)
        self.assertIn(b"password-toggle", login.data)
        self.assertIn(b'class="auth-page"', login.data)
        self.assertEqual(register.status_code, 200)
        self.assertIn(b'name="name"', register.data)
        self.assertIn(b'At least 10 characters', register.data)

    def test_registration_requires_email_otp_before_creating_account(self):
        response = self.client.post(
            "/register",
            data={
                "name": "New Customer",
                "email": "new@example.com",
                "password": "secure-password-123",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/verify-email")
        self.assertNotIn("new@example.com", self.database.created_users)
        pending = self.database.otp_verifications["new@example.com"]
        self.assertEqual(len(pending["otp_hash"]), 64)
        self.assertNotIn("secure-password-123", pending["password_hash"])
        self.assertIn("new@example.com", self.mailer.messages[0][0])

        verify_page = self.client.get("/verify-email")
        self.assertEqual(verify_page.status_code, 200)
        self.assertIn(b'name="otp"', verify_page.data)
        code = re.search(r"code is (\d{6})", self.mailer.messages[0][2]).group(1)
        verified = self.client.post("/verify-email", data={"otp": code})

        self.assertEqual(verified.status_code, 302)
        self.assertEqual(verified.headers["Location"], "/login")
        self.assertIn("new@example.com", self.database.created_users)
        self.assertNotIn("new@example.com", self.database.otp_verifications)
        self.assertEqual(len(self.mailer.messages), 2)

    def test_registration_reports_smtp_failure_without_creating_account(self):
        self.mailer.send_success = False

        response = self.client.post(
            "/register",
            data={
                "name": "New Customer",
                "email": "new@example.com",
                "password": "secure-password-123",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Hindi naipadala ang verification code", response.data)
        self.assertNotIn("new@example.com", self.database.created_users)

    def test_registration_requires_gmail_smtp_configuration(self):
        self.mailer.configured = False

        response = self.client.post(
            "/register",
            data={
                "name": "New Customer",
                "email": "new@example.com",
                "password": "secure-password-123",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Hindi pa naka-configure ang Gmail SMTP", response.data)
        self.assertFalse(self.database.otp_verifications)
        self.assertFalse(self.mailer.messages)

    def test_gmail_mailer_uses_starttls_and_a_normalized_app_password(self):
        mailer = Mailer(
            {
                "SMTP_HOST": "smtp.gmail.com",
                "SMTP_PORT": 587,
                "SMTP_USER": "shop@gmail.com",
                "SMTP_PASSWORD": "abcd efgh ijkl mnop",
                "MAIL_FROM": "shop@gmail.com",
            }
        )
        with patch("mailer.smtplib.SMTP") as smtp:
            server = smtp.return_value.__enter__.return_value

            self.assertTrue(mailer.send("customer@example.com", "OTP", "123456"))

        smtp.assert_called_once_with("smtp.gmail.com", 587, timeout=10)
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("shop@gmail.com", "abcdefghijklmnop")
        server.send_message.assert_called_once()

    def test_expired_otp_and_five_attempt_limit_are_enforced(self):
        with patch("app.secrets.randbelow", return_value=123456):
            self.client.post(
                "/register",
                data={
                    "name": "New Customer",
                    "email": "new@example.com",
                    "password": "secure-password-123",
                },
            )
            code = re.search(
                r"code is (\d{6})", self.mailer.messages[0][2]
            ).group(1)

            pending = self.database.otp_verifications["new@example.com"]
            pending["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
            expired = self.client.post("/verify-email", data={"otp": code})
            self.assertIn(b"Nag-expire na ang code", expired.data)

            pending["expires_at"] = datetime.now(timezone.utc) + timedelta(minutes=10)
            for _ in range(5):
                self.client.post("/verify-email", data={"otp": "000000"})
            locked = self.client.post("/verify-email", data={"otp": code})

        self.assertIn(b"Masyadong maraming maling pagtatangka", locked.data)
        self.assertEqual(pending["attempts"], 5)
        self.assertNotIn("new@example.com", self.database.created_users)

    def test_registration_otp_resend_is_throttled_and_replaces_the_old_code(self):
        with patch("app.secrets.randbelow", side_effect=[111111, 222222]):
            self.client.post(
                "/register",
                data={
                    "name": "New Customer",
                    "email": "new@example.com",
                    "password": "secure-password-123",
                },
            )
            original_code = re.search(
                r"code is (\d{6})", self.mailer.messages[0][2]
            ).group(1)

            throttled = self.client.post("/verify-email/resend", follow_redirects=True)
            self.assertIn(b"Maghintay ng", throttled.data)
            self.assertEqual(len(self.mailer.messages), 1)

            self.database.otp_verifications["new@example.com"]["created_at"] = (
                datetime.now(timezone.utc) - timedelta(seconds=61)
            )
            resent = self.client.post("/verify-email/resend")
            self.assertEqual(resent.status_code, 302)
            self.assertEqual(len(self.mailer.messages), 2)
            new_code = re.search(r"code is (\d{6})", self.mailer.messages[1][2]).group(1)
            self.assertNotEqual(original_code, new_code)

            verified = self.client.post("/verify-email", data={"otp": new_code})
            self.assertEqual(verified.headers["Location"], "/login")
            self.assertIn("new@example.com", self.database.created_users)

    def test_logout_uses_english_message_that_auto_dismisses(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        response = self.client.post("/logout", follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"You have been signed out.", response.data)
        self.assertIn(b'class="flash flash-logout"', response.data)

    def test_category_filter_uses_a_sql_parameter(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        response = self.client.get("/shop?category=T-Shirts")
        self.assertEqual(response.status_code, 200)
        query, params = next(
            (query, params)
            for query, params in self.database.queries
            if "category = %s" in query
        )
        self.assertEqual(params[0], "T-Shirts")
        self.assertNotIn("T-Shirts", query)

    def test_shop_renders_collection_controls_without_products(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        response = self.client.get("/shop")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"SHOP COLLECTION", response.data)
        self.assertIn(b"All Products", response.data)
        self.assertIn(b'<span>T-Shirts</span>', response.data)
        self.assertIn(b'<span>Hoodies</span>', response.data)
        self.assertIn(b'<span>Pants</span>', response.data)
        self.assertIn(b'<span>Accessories</span>', response.data)
        self.assertIn(b'name="min_price"', response.data)
        self.assertIn(b'name="max_price"', response.data)
        self.assertIn(b'value="newest"', response.data)
        self.assertIn(b"No products available right now", response.data)
        self.assertIn(b"Our shop is getting ready", response.data)
        self.assertNotIn(b'aria-label="Add GP Essential Tee to favorites"', response.data)
        self.assertNotIn(b'aria-label="Choose size for GP Essential Tee"', response.data)
        self.assertNotIn(b"poster-graphic-tee.png", response.data)
        self.assertIn(b'aria-label="Grid view"', response.data)
        self.assertIn(b'aria-label="List view"', response.data)

    def test_shop_color_filter_uses_a_sql_parameter(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        response = self.client.get("/shop?color=blue")
        self.assertEqual(response.status_code, 200)
        query, params = next(
            (query, params)
            for query, params in self.database.queries
            if "name LIKE %s OR description LIKE %s" in query
        )
        self.assertEqual(params[-2:], ("%blue%", "%blue%"))
        self.assertNotIn("%blue%", query)

    def test_seed_replaces_the_sample_catalog_with_three_equal_price_tees(self):
        result = self.app.test_cli_runner().invoke(args=["seed"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("The three Gameplan T-shirts are ready.", result.output)
        self.assertIn(
            ("UPDATE products SET active = FALSE WHERE active = TRUE", ()),
            self.database.executions,
        )
        products = [
            params
            for query, params in self.database.executions
            if "INSERT INTO products" in query
        ]
        self.assertEqual(len(products), 3)
        self.assertEqual(
            {product[1] for product in products},
            {"poster-graphic-tee", "gameplan-blue-logo-tee", "all-in-statement-tee"},
        )
        self.assertEqual({product[4] for product in products}, {600})

    def test_tshirt_detail_has_gallery_size_guide_and_quantity_controls(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        response = self.client.get("/products/1")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"GAMEPLAN APPAREL CO.", response.data)
        self.assertIn(b'name="size"', response.data)
        self.assertIn(b'value="S"', response.data)
        self.assertIn(b'value="L"', response.data)
        self.assertIn(b"Size Guide", response.data)
        self.assertIn(b'data-quantity-step="-1"', response.data)
        self.assertIn(b"Fast Delivery", response.data)
        self.assertIn(b'src="/static/img/poster-graphic-tee.png"', response.data)

    def test_tshirt_size_is_required_and_preserved_in_cart(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        missing_size = self.client.post("/cart/add/1", data={"quantity": "1"})
        self.assertEqual(missing_size.status_code, 302)
        self.assertEqual(missing_size.headers["Location"], "/products/1")
        self.assertEqual(self.client.get("/cart").status_code, 200)
        with self.client.session_transaction() as store:
            self.assertEqual(store.get("cart", {}), {})

        added = self.client.post(
            "/cart/add/1",
            data={"quantity": "2", "size": "L", "return_to": "/products/1"},
        )
        self.assertEqual(added.headers["Location"], "http://localhost/products/1")
        self.client.post("/cart/add/1", data={"quantity": "9", "size": "M"})
        with self.client.session_transaction() as store:
            self.assertEqual(store["cart"], {"1:L": 2})
        cart = self.client.get("/cart")
        self.assertEqual(cart.status_code, 200)
        self.assertIn(b"Size L", cart.data)
        self.assertIn(b'value="2"', cart.data)

        removed = self.client.post("/cart/remove/1", data={"size": "L"})
        self.assertEqual(removed.status_code, 302)
        with self.client.session_transaction() as store:
            self.assertEqual(store.get("cart", {}), {})

    def test_cart_update_shows_success_or_stock_error_feedback(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        with self.client.session_transaction() as store:
            store["cart"] = {"1:M": 2}

        updated = self.client.post(
            "/cart/update/1",
            data={"quantity": "3", "size": "M"},
            follow_redirects=True,
        )

        self.assertEqual(updated.status_code, 200)
        self.assertIn(b"Na-update ang dami ng produkto sa bag.", updated.data)
        self.assertIn(b'value="3"', updated.data)
        with self.client.session_transaction() as store:
            self.assertEqual(store["cart"], {"1:M": 3})

        rejected = self.client.post(
            "/cart/update/1",
            data={"quantity": "11", "size": "M"},
            follow_redirects=True,
        )

        self.assertEqual(rejected.status_code, 200)
        self.assertIn(b"Hindi sapat ang stock", rejected.data)
        with self.client.session_transaction() as store:
            self.assertEqual(store["cart"], {"1:M": 3})

    def test_cart_update_with_zero_quantity_removes_item_with_feedback(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        with self.client.session_transaction() as store:
            store["cart"] = {"1:M": 2}

        response = self.client.post(
            "/cart/update/1",
            data={"quantity": "0", "size": "M"},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Inalis ang produkto sa bag.", response.data)
        self.assertIn(b"Your bag is taking a breather.", response.data)

    def test_shop_sorting_uses_a_safe_ordering_choice(self):
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        response = self.client.get("/shop?sort=price-low")
        self.assertEqual(response.status_code, 200)
        query, _params = next(
            (query, params)
            for query, params in self.database.queries
            if "SELECT id, name, slug" in query
        )
        self.assertIn("ORDER BY price ASC, created_at DESC", query)

        self.client.get("/shop?sort=price-low%20DESC")
        query, _params = next(
            (query, params)
            for query, params in reversed(self.database.queries)
            if "SELECT id, name, slug" in query
        )
        self.assertIn("ORDER BY created_at DESC", query)
        self.assertNotIn("price-low", query)

    def test_login_rejects_external_redirect_target(self):
        response = self.client.post(
            "/login?next=https://example.invalid",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_login_from_home_redirects_to_homepage_without_product_listings(self):
        response = self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")
        homepage = self.client.get(response.headers["Location"])
        self.assertEqual(homepage.status_code, 200)
        self.assertIn(b"Shop by Category", homepage.data)
        self.assertNotIn(b"GP Essential Tee", homepage.data)

    def test_csrf_rejects_unprotected_cart_post(self):
        csrf_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "DATABASE": self.database,
                "WTF_CSRF_ENABLED": True,
            }
        )
        response = csrf_app.test_client().post("/cart/add/1")
        self.assertEqual(response.status_code, 400)

    def test_admin_can_view_orders_and_update_order_status(self):
        admin_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "WTF_CSRF_ENABLED": False,
                "DATABASE": self.database,
                "ADMIN_EMAIL": "customer@example.com",
            }
        )
        admin_client = admin_app.test_client()
        login = admin_client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )
        self.assertEqual(login.status_code, 302)

        orders = admin_client.get("/admin/orders")
        self.assertEqual(orders.status_code, 200)
        self.assertIn(b"Order management", orders.data)
        self.assertIn(b"Awaiting acceptance", orders.data)
        self.assertIn(b"Accept order", orders.data)
        self.assertIn(b"#1", orders.data)
        self.assertIn(b"Gameplan Customer", orders.data)

        detail = admin_client.get("/admin/orders/1")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Shipping address", detail.data)
        self.assertIn(b"Update order status", detail.data)
        self.assertIn(b"GP Essential Tee", detail.data)

        accepted = admin_client.post(
            "/admin/orders/1/status",
            data={"status": "processing", "return_to": "orders"},
        )
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(accepted.headers["Location"], "/admin/orders")
        self.assertIn(
            ("UPDATE orders SET status = %s WHERE id = %s", ("processing", 1)),
            self.database.executions,
        )

        updated = admin_client.post(
            "/admin/orders/1/status",
            data={"status": "processing"},
        )
        self.assertEqual(updated.status_code, 302)
        self.assertEqual(updated.headers["Location"], "/admin/orders/1")

    def test_admin_product_form_uploads_and_previews_an_image(self):
        self.app.config.update(
            {
                "ADMIN_EMAIL": "customer@example.com",
                "PRODUCT_UPLOAD_FOLDER": self.get_temp_upload_folder(),
            }
        )
        self.client.post(
            "/login",
            data={"email": "customer@example.com", "password": "secure-password-123"},
        )

        form = self.client.get("/admin/products/new")
        self.assertEqual(form.status_code, 200)
        self.assertIn(b'class="product-image-upload-box"', form.data)
        self.assertIn(b'type="file"', form.data)
        self.assertNotIn(b"Image URL", form.data)

        response = self.client.post(
            "/admin/products/new",
            data={
                "name": "Uploaded Tee",
                "slug": "uploaded-tee",
                "description": "A test product",
                "category": "T-Shirts",
                "price": "600",
                "stock": "5",
                "image": (BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4), "tee.png"),
            },
        )

        self.assertEqual(response.status_code, 302)
        _query, params = next(
            (query, params)
            for query, params in self.database.executions
            if "INSERT INTO products" in query
        )
        self.assertTrue(params[5].startswith("/static/images/products/"))
        self.assertTrue(
            (Path(self.app.config["PRODUCT_UPLOAD_FOLDER"]) / Path(params[5]).name).is_file()
        )

        execution_count = len(self.database.executions)
        invalid = self.client.post(
            "/admin/products/new",
            data={
                "name": "Bad Image Tee",
                "slug": "bad-image-tee",
                "description": "A test product",
                "category": "T-Shirts",
                "price": "600",
                "stock": "5",
                "image": (BytesIO(b"not an image"), "tee.png"),
            },
        )
        self.assertIn(b"valid na JPG, PNG, o WebP", invalid.data)
        self.assertEqual(len(self.database.executions), execution_count)

    def get_temp_upload_folder(self):
        if not hasattr(self, "_upload_directory"):
            self._upload_directory = TemporaryDirectory()
            self.addCleanup(self._upload_directory.cleanup)
        return self._upload_directory.name


if __name__ == "__main__":
    unittest.main()
