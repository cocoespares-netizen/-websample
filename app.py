import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path
from uuid import uuid4
from urllib.parse import urljoin, urlsplit

import click
from dotenv import load_dotenv
from mysql.connector.errors import IntegrityError

load_dotenv()

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash

from config import Config
from database import Database
from mailer import Mailer
from models import Catalog, User

csrf = CSRFProtect()
login_manager = LoginManager()
APPAREL_SIZES = ("S", "M", "L")
APPAREL_CATEGORIES = ("T-Shirts", "Hoodies", "Pants", "Accessories")
login_manager.login_view = "login"
login_manager.login_message = "Mag-sign in muna para buksan ang pahinang ito."
login_manager.login_message_category = "info"


@login_manager.user_loader
def load_user(user_id):
    return User.get(current_app.extensions["database"], user_id)


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config.setdefault("MAX_CONTENT_LENGTH", 8 * 1024 * 1024)
    app.config.setdefault(
        "PRODUCT_UPLOAD_FOLDER",
        str(Path(app.static_folder) / "images" / "products"),
    )
    if test_config:
        app.config.update(test_config)
    database = app.config.get("DATABASE") or Database(app.config)
    app.extensions["database"] = database
    app.extensions["database_initialized"] = app.config.get("DATABASE") is not None
    app.extensions["mailer"] = app.config.get("MAILER") or Mailer(app.config)
    csrf.init_app(app)
    login_manager.init_app(app)

    def ensure_database():
        if not app.extensions["database_initialized"]:
            database.initialize()
            app.extensions["database_initialized"] = True

    @app.before_request
    def initialize_database():
        ensure_database()

    def db():
        return app.extensions["database"]

    def catalog():
        return Catalog(db())

    def safe_local_url(target):
        if not target:
            return None
        reference = urlsplit(request.host_url)
        candidate = urlsplit(urljoin(request.host_url, target))
        if (
            candidate.scheme in ("http", "https")
            and candidate.netloc == reference.netloc
            and candidate.path.startswith("/")
        ):
            return candidate.geturl()
        return None

    def admin_required(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not current_user.is_admin:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    def cart_items():
        items = []
        total = Decimal("0.00")
        for cart_key, quantity in session.get("cart", {}).items():
            product_id, separator, size = cart_key.partition(":")
            product = catalog().get(int(product_id))
            if product:
                line_total = Decimal(str(product.price)) * quantity
                total += line_total
                items.append(
                    {
                        "product": product,
                        "quantity": quantity,
                        "size": size if separator else None,
                        "cart_key": cart_key,
                        "line_total": line_total,
                    }
                )
        return items, total

    def cart_product_quantity(cart, product_id, exclude_key=None):
        return sum(
            quantity
            for key, quantity in cart.items()
            if key != exclude_key and key.partition(":")[0] == str(product_id)
        )

    def otp_digest(email, code):
        secret = str(app.config["SECRET_KEY"]).encode("utf-8")
        return hmac.new(
            secret,
            f"{email}:{code}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def generate_otp():
        return f"{secrets.randbelow(1_000_000):06d}"

    def utc_now():
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def send_otp(email, code):
        return app.extensions["mailer"].send(
            email,
            "Your Gameplan Apparel verification code",
            f"Your verification code is {code}.\n\n"
            "It expires in 10 minutes. Do not share this code with anyone.",
        )

    def otp_record(email):
        return db().query(
            "SELECT email, name, password_hash, role, otp_hash, expires_at, "
            "created_at, attempts FROM otp_verifications WHERE email = %s",
            (email,),
            one=True,
        )

    def resend_wait_seconds(record):
        created_at = record["created_at"]
        if created_at.tzinfo is not None:
            created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)
        elapsed = (utc_now() - created_at).total_seconds()
        return max(0, 60 - int(elapsed))

    @app.context_processor
    def inject_store_data():
        categories = list(APPAREL_CATEGORIES)
        categories.extend(
            category
            for category in catalog().categories()
            if category not in categories
        )
        return {
            "cart_count": sum(session.get("cart", {}).values()),
            "categories": categories,
        }

    @app.get("/")
    def home():
        return render_template("home.html")

    @app.get("/about")
    def about():
        return render_template("about.html")

    @app.get("/lookbook")
    def lookbook():
        return render_template("lookbook.html")

    @app.get("/collections")
    @login_required
    def collections():
        category = request.args.get("category", "").strip()
        search = request.args.get("q", "").strip()[:100]
        sort = request.args.get("sort", "newest")
        if sort not in {"newest", "price-low", "price-high"}:
            sort = "newest"
        size = request.args.get("size", "").strip().upper()
        if size not in APPAREL_SIZES:
            size = ""
        try:
            minimum = Decimal(request.args["min_price"]) if request.args.get("min_price") else None
            maximum = Decimal(request.args["max_price"]) if request.args.get("max_price") else None
            if (minimum is not None and minimum < 0) or (maximum is not None and maximum < 0):
                raise InvalidOperation
        except InvalidOperation:
            flash("Hindi wasto ang price filter.", "error")
            minimum, maximum = None, None
        products = catalog().list(
            category, search, minimum, maximum, sort, size=size
        )
        return render_template(
            "collections.html",
            products=products,
            selected_category=category,
            selected_size=size,
            selected_sort=sort,
            search=search,
        )

    @app.get("/shop")
    @login_required
    def shop():
        category = request.args.get("category", "").strip()
        search = request.args.get("q", "").strip()[:100]
        sort = request.args.get("sort", "newest")
        if sort not in {"newest", "price-low", "price-high"}:
            sort = "newest"
        color = request.args.get("color", "").strip().lower()
        if color not in {"black", "blue"}:
            color = ""
        size = request.args.get("size", "").strip().upper()
        if size not in APPAREL_SIZES:
            size = ""
        try:
            minimum = Decimal(request.args["min_price"]) if request.args.get("min_price") else None
            maximum = Decimal(request.args["max_price"]) if request.args.get("max_price") else None
            if (minimum is not None and minimum < 0) or (maximum is not None and maximum < 0):
                raise InvalidOperation
        except InvalidOperation:
            flash("Hindi wasto ang price filter.", "error")
            minimum, maximum = None, None
        products = catalog().list(category, search, minimum, maximum, sort, color, size)
        return render_template(
            "shop.html",
            products=products,
            selected_category=category,
            search=search,
            selected_sort=sort,
            selected_color=color,
            selected_size=size,
        )

    @app.get("/products/<int:product_id>")
    @login_required
    def product_detail(product_id):
        product = catalog().get(product_id)
        if not product:
            abort(404)
        detail_images = [product.image_url] if product.image_url else []
        return render_template(
            "product.html",
            product=product,
            detail_images=detail_images,
            sizes=APPAREL_SIZES if product.category == "T-Shirts" else (),
        )

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        admin_email = app.config.get("ADMIN_EMAIL", "").strip().lower()
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            if (
                not name
                or len(name) > 100
                or len(email) > 254
                or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
            ):
                flash("Ilagay ang wastong pangalan at email address.", "error")
            elif len(password) < 10:
                flash("Dapat hindi bababa sa 10 character ang password.", "error")
            elif User.by_email(db(), email):
                flash("May account na gamit ang email na ito.", "error")
            elif not app.extensions["mailer"].configured:
                flash(
                    "Hindi pa naka-configure ang Gmail SMTP. Ayusin muna ang SMTP settings bago mag-register.",
                    "error",
                )
            else:
                existing_otp = otp_record(email)
                if existing_otp and resend_wait_seconds(existing_otp):
                    session["pending_otp_email"] = email
                    flash(
                        "May pending na verification para sa email na ito. Tingnan ang inbox o spam "
                        "folder mo; maaari kang humiling ulit pagkalipas ng 60 segundo.",
                        "info",
                    )
                    return redirect(url_for("verify_email"))

                code = generate_otp()
                db().execute(
                    "INSERT INTO otp_verifications "
                    "(email, name, password_hash, role, otp_hash, expires_at, attempts, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 0, %s) "
                    "ON DUPLICATE KEY UPDATE name=VALUES(name), "
                    "password_hash=VALUES(password_hash), role=VALUES(role), "
                    "otp_hash=VALUES(otp_hash), expires_at=VALUES(expires_at), "
                    "attempts=0, created_at=VALUES(created_at)",
                    (
                        email,
                        name,
                        generate_password_hash(password),
                        "admin" if email == admin_email else "customer",
                        otp_digest(email, code),
                        utc_now() + timedelta(minutes=10),
                        utc_now(),
                    ),
                )
                session["pending_otp_email"] = email
                if not send_otp(email, code):
                    flash(
                        "Hindi naipadala ang verification code. Suriin ang Gmail SMTP settings "
                        "at subukan muli pagkalipas ng 60 segundo.",
                        "error",
                    )
                else:
                    flash("Nagpadala kami ng 6-digit code sa email mo.", "success")
                return redirect(url_for("verify_email"))
        return render_template("register.html")

    @app.route("/verify-email", methods=["GET", "POST"])
    def verify_email():
        email = session.get("pending_otp_email")
        if not email:
            return redirect(url_for("register"))
        record = otp_record(email)
        if not record:
            session.pop("pending_otp_email", None)
            flash("Walang pending na verification. Mag-register ulit.", "error")
            return redirect(url_for("register"))

        error = None
        if request.method == "POST":
            code = request.form.get("otp", "").strip()
            if not re.fullmatch(r"\d{6}", code):
                error = "Ilagay ang 6-digit verification code."
            elif record["attempts"] >= 5:
                error = "Masyadong maraming maling pagtatangka. Humiling ng bagong code."
            else:
                expires_at = record["expires_at"]
                if expires_at.tzinfo is not None:
                    expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
                if utc_now() >= expires_at:
                    error = "Nag-expire na ang code. Humiling ng bago."
                elif not hmac.compare_digest(record["otp_hash"], otp_digest(email, code)):
                    db().execute(
                        "UPDATE otp_verifications SET attempts = attempts + 1 WHERE email = %s",
                        (email,),
                    )
                    error = "Hindi tama ang code. Suriin ito at subukan muli."
                else:
                    try:
                        with db().transaction() as connection:
                            cursor = connection.cursor()
                            try:
                                cursor.execute(
                                    "INSERT INTO users (name, email, password_hash, role) "
                                    "VALUES (%s, %s, %s, %s)",
                                    (
                                        record["name"],
                                        email,
                                        record["password_hash"],
                                        record["role"],
                                    ),
                                )
                                cursor.execute(
                                    "DELETE FROM otp_verifications WHERE email = %s",
                                    (email,),
                                )
                            finally:
                                cursor.close()
                    except IntegrityError:
                        db().execute(
                            "DELETE FROM otp_verifications WHERE email = %s",
                            (email,),
                        )
                        session.pop("pending_otp_email", None)
                        flash("May account na gamit ang email na ito.", "error")
                        return redirect(url_for("register"))

                    session.pop("pending_otp_email", None)
                    mailer = app.extensions["mailer"]
                    if not mailer.send(
                        email,
                        "Welcome to Gameplan Apparel",
                        f"Hi {record['name']},\n\n"
                        "Your Gameplan Apparel account is verified and ready.",
                    ):
                        flash(
                            "Verified na ang account, pero hindi naipadala ang welcome email.",
                            "warning",
                        )
                    flash("Verified na ang email at nagawa na ang account. Mag-sign in na.", "success")
                    return redirect(url_for("login"))
        return render_template("verify_email.html", email=email, error=error)

    @app.post("/verify-email/resend")
    def resend_verification_email():
        email = session.get("pending_otp_email")
        if not email:
            return redirect(url_for("register"))
        record = otp_record(email)
        if not record:
            session.pop("pending_otp_email", None)
            flash("Walang pending na verification. Mag-register ulit.", "error")
            return redirect(url_for("register"))
        wait_seconds = resend_wait_seconds(record)
        if wait_seconds:
            flash(
                f"Maghintay ng {wait_seconds} segundo bago humiling ng bagong code.",
                "info",
            )
            return redirect(url_for("verify_email"))

        code = generate_otp()
        db().execute(
            "UPDATE otp_verifications SET otp_hash = %s, expires_at = %s, "
            "attempts = 0, created_at = %s WHERE email = %s",
            (
                otp_digest(email, code),
                utc_now() + timedelta(minutes=10),
                utc_now(),
                email,
            ),
        )
        if not send_otp(email, code):
            flash(
                "Hindi naipadala ang bagong code. Suriin ang Gmail SMTP settings at subukan muli.",
                "error",
            )
            return redirect(url_for("verify_email"))
        flash("Nagpadala kami ng bagong verification code.", "success")
        return redirect(url_for("verify_email"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("home"))
        if request.method == "POST":
            user = User.by_email(db(), request.form.get("email", "").strip().lower())
            password = request.form.get("password", "")
            if user and user.verify_password(password):
                login_user(user, remember=request.form.get("remember") == "yes")
                session.permanent = True
                return redirect(safe_local_url(request.args.get("next")) or url_for("home"))
            flash("Hindi tugma ang email o password.", "error")
        return render_template("login.html")

    @app.post("/logout")
    @login_required
    def logout():
        logout_user()
        session.pop("cart", None)
        flash("You have been signed out.", "logout")
        return redirect(url_for("home"))

    @app.get("/dashboard")
    @login_required
    def dashboard():
        orders = db().query(
            "SELECT id, total, status, created_at FROM orders "
            "WHERE user_id = %s ORDER BY created_at DESC LIMIT 10",
            (current_user.id,),
        )
        return render_template("dashboard.html", orders=orders)

    @app.route("/profile", methods=["GET", "POST"])
    @login_required
    def profile():
        admin_email = app.config.get("ADMIN_EMAIL", "").strip().lower()
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            if (
                not name
                or len(name) > 100
                or len(email) > 254
                or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
            ):
                flash("Ilagay ang wastong pangalan at email address.", "error")
            elif request.form.get("new_password") and len(request.form.get("new_password", "")) < 10:
                flash("Dapat hindi bababa sa 10 character ang bagong password.", "error")
            else:
                existing = User.by_email(db(), email)
                if existing and existing.id != current_user.id:
                    flash("Ginagamit na ng ibang account ang email address na iyan.", "error")
                elif request.form.get("new_password") and not User.get(
                    db(), current_user.id
                ).verify_password(request.form.get("current_password", "")):
                    flash("Hindi tama ang kasalukuyang password.", "error")
                else:
                    new_password = request.form.get("new_password", "")
                    role = "admin" if email == admin_email else "customer"
                    if new_password:
                        db().execute(
                            "UPDATE users SET name = %s, email = %s, password_hash = %s, role = %s WHERE id = %s",
                            (name, email, generate_password_hash(new_password), role, current_user.id),
                        )
                    else:
                        db().execute(
                            "UPDATE users SET name = %s, email = %s, role = %s WHERE id = %s",
                            (name, email, role, current_user.id),
                        )
                    flash("Na-update ang profile.", "success")
                    return redirect(url_for("profile"))
        fresh_user = User.get(db(), current_user.id)
        return render_template("profile.html", profile_user=fresh_user)

    @app.post("/cart/add/<int:product_id>")
    @login_required
    def add_to_cart(product_id):
        product = catalog().get(product_id)
        if not product:
            abort(404)
        size = request.form.get("size", "").strip().upper() if product.category == "T-Shirts" else ""
        if product.category == "T-Shirts" and size not in APPAREL_SIZES:
            flash("Pumili muna ng wastong laki ng T-shirt.", "error")
            return redirect(url_for("product_detail", product_id=product_id))
        try:
            quantity = int(request.form.get("quantity", "1"))
        except ValueError:
            quantity = 0
        cart_key = f"{product_id}:{size}" if size else str(product_id)
        if quantity < 1 or quantity > 10:
            flash("Pumili ng 1 hanggang 10 piraso.", "error")
        elif cart_product_quantity(session.get("cart", {}), product_id) + quantity > product.stock:
            flash("Hindi sapat ang stock para sa dami na pinili.", "error")
        else:
            cart = session.get("cart", {})
            cart[cart_key] = cart.get(cart_key, 0) + quantity
            session["cart"] = cart
            flash(f"Naidagdag sa bag ang {product.name}.", "success")
        return_to = safe_local_url(request.form.get("return_to"))
        if return_to:
            return redirect(return_to)
        return redirect(url_for("shop"))

    @app.get("/cart")
    @login_required
    def cart():
        items, subtotal = cart_items()
        return render_template("cart.html", items=items, subtotal=subtotal)

    @app.post("/cart/update/<int:product_id>")
    @login_required
    def update_cart(product_id):
        product = catalog().get(product_id)
        if not product:
            abort(404)
        size = request.form.get("size", "").strip().upper() if product.category == "T-Shirts" else ""
        cart_key = f"{product_id}:{size}" if size in APPAREL_SIZES else str(product_id)
        try:
            quantity = int(request.form.get("quantity", "1"))
        except ValueError:
            quantity = 0
        cart = session.get("cart", {})
        if quantity <= 0:
            cart.pop(cart_key, None)
            flash("Inalis ang produkto sa bag.", "success")
        elif (
            quantity > 10
            or cart_product_quantity(cart, product_id, cart_key) + quantity > product.stock
        ):
            flash("Hindi sapat ang stock para sa dami na pinili.", "error")
        else:
            cart[cart_key] = quantity
            flash("Na-update ang dami ng produkto sa bag.", "success")
        session["cart"] = cart
        return redirect(url_for("cart"))

    @app.post("/cart/remove/<int:product_id>")
    @login_required
    def remove_from_cart(product_id):
        cart = session.get("cart", {})
        size = request.form.get("size", "").strip().upper()
        cart_key = f"{product_id}:{size}" if size in APPAREL_SIZES else str(product_id)
        cart.pop(cart_key, None)
        session["cart"] = cart
        return redirect(url_for("cart"))

    @app.route("/checkout", methods=["GET", "POST"])
    @login_required
    def checkout():
        items, subtotal = cart_items()
        if not items:
            flash("Walang laman ang shopping bag mo.", "info")
            return redirect(url_for("cart"))
        shipping_fee = Decimal("100.00")
        total = subtotal + shipping_fee
        if request.method == "POST":
            fields = {
                key: request.form.get(key, "").strip()
                for key in (
                    "full_name",
                    "phone",
                    "email",
                    "address",
                    "barangay",
                    "city",
                    "province",
                    "postal_code",
                )
            }
            if any(not value or len(value) > 255 for value in fields.values()):
                flash("Punan nang wasto ang lahat ng kinakailangang shipping field.", "error")
            elif (
                len(fields["email"]) > 254
                or len(fields["phone"]) > 30
                or len(fields["barangay"]) > 100
                or len(fields["postal_code"]) > 20
            ):
                flash("May shipping field na lumampas sa pinapayagang haba.", "error")
            elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", fields["email"]):
                flash("Hindi wasto ang email address.", "error")
            elif not re.fullmatch(r"\+?[\d ()-]{7,30}", fields["phone"]) or sum(
                character.isdigit() for character in fields["phone"]
            ) < 7:
                flash("Ilagay ang wastong contact number.", "error")
            elif request.form.get("payment_method") not in ("cod", "gcash", "bank_transfer"):
                flash("Pumili ng wastong payment method.", "error")
            else:
                try:
                    with db().transaction() as connection:
                        cursor = connection.cursor()
                        cursor.execute(
                            "INSERT INTO orders (user_id, full_name, phone, email, address, barangay, "
                            "city, province, postal_code, payment_method, subtotal, shipping_fee, total) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            (
                                current_user.id,
                                fields["full_name"],
                                fields["phone"],
                                fields["email"],
                                fields["address"],
                                fields["barangay"],
                                fields["city"],
                                fields["province"],
                                fields["postal_code"],
                                request.form["payment_method"],
                                subtotal,
                                shipping_fee,
                                total,
                            ),
                        )
                        order_id = cursor.lastrowid
                        stock_short = False
                        for item in items:
                            cursor.execute(
                                "UPDATE products SET stock = stock - %s "
                                "WHERE id = %s AND stock >= %s AND active = TRUE",
                                (
                                    item["quantity"],
                                    item["product"].id,
                                    item["quantity"],
                                ),
                            )
                            if cursor.rowcount != 1:
                                stock_short = True
                                break
                            cursor.execute(
                                "INSERT INTO order_items (order_id, product_id, product_name, size, "
                                "unit_price, quantity) VALUES (%s, %s, %s, %s, %s, %s)",
                                (
                                    order_id,
                                    item["product"].id,
                                    item["product"].name,
                                    item["size"],
                                    item["product"].price,
                                    item["quantity"],
                                ),
                            )
                        cursor.close()
                        if stock_short:
                            raise ValueError("Insufficient stock")
                except ValueError:
                    flash("May produktong naubusan na ng stock. Suriin muli ang bag mo.", "error")
                    return redirect(url_for("cart"))
                session["cart"] = {}
                mailer = app.extensions["mailer"]
                sent = mailer.send(
                    fields["email"],
                    f"Gameplan order #{order_id} received",
                    f"Hi {fields['full_name']},\n\nWe received your order #{order_id} "
                    f"for PHP {total:.2f}. Payment method: {request.form['payment_method']}.",
                )
                if not sent:
                    flash("Hindi naipadala ang order confirmation email; nakatala pa rin ang order.", "warning")
                flash(f"Natanggap ang order #{order_id}.", "success")
                return redirect(url_for("dashboard"))
        return render_template(
            "checkout.html",
            items=items,
            subtotal=subtotal,
            shipping_fee=shipping_fee,
            total=total,
        )

    @app.get("/admin/products")
    @admin_required
    def admin_products():
        products = db().query("SELECT * FROM products ORDER BY created_at DESC")
        return render_template("admin_products.html", products=products)

    @app.get("/admin/orders")
    @admin_required
    def admin_orders():
        orders = db().query(
            "SELECT o.id, o.full_name, o.email, o.phone, o.payment_method, o.status, "
            "o.total, o.created_at, u.name AS customer_name "
            "FROM orders o LEFT JOIN users u ON u.id = o.user_id "
            "ORDER BY o.created_at DESC"
        )
        pending_count = sum(order["status"] == "pending" for order in orders)
        order_value = sum(
            (Decimal(str(order["total"])) for order in orders),
            Decimal("0.00"),
        )
        return render_template(
            "admin_orders.html",
            orders=orders,
            pending_count=pending_count,
            order_value=order_value,
        )

    @app.get("/admin/orders/<int:order_id>")
    @admin_required
    def admin_order_detail(order_id):
        order = db().query(
            "SELECT o.*, u.name AS customer_name "
            "FROM orders o LEFT JOIN users u ON u.id = o.user_id "
            "WHERE o.id = %s",
            (order_id,),
            one=True,
        )
        if not order:
            abort(404)
        items = db().query(
            "SELECT product_name, size, unit_price, quantity FROM order_items WHERE order_id = %s ORDER BY id",
            (order_id,),
        )
        return render_template("admin_order_detail.html", order=order, items=items)

    @app.post("/admin/orders/<int:order_id>/status")
    @admin_required
    def update_order_status(order_id):
        order = db().query("SELECT id FROM orders WHERE id = %s", (order_id,), one=True)
        if not order:
            abort(404)
        return_to_orders = request.form.get("return_to") == "orders"
        destination = (
            url_for("admin_orders")
            if return_to_orders
            else url_for("admin_order_detail", order_id=order_id)
        )
        status = request.form.get("status", "pending")
        valid_statuses = {"pending", "processing", "shipped", "completed", "cancelled"}
        if status not in valid_statuses:
            flash("Hindi valid ang status ng order.", "error")
            return redirect(destination)
        db().execute("UPDATE orders SET status = %s WHERE id = %s", (status, order_id))
        flash("Na-update ang status ng order.", "success")
        return redirect(destination)

    @app.route("/admin/products/new", methods=["GET", "POST"])
    @app.route("/admin/products/<int:product_id>/edit", methods=["GET", "POST"])
    @admin_required
    def edit_product(product_id=None):
        product = db().query(
            "SELECT * FROM products WHERE id = %s", (product_id,), one=True
        ) if product_id else None
        if product_id and not product:
            abort(404)
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            slug = request.form.get("slug", "").strip().lower()
            description = request.form.get("description", "").strip()
            category = request.form.get("category", "").strip()
            price_input = request.form.get("price", "").strip()
            stock_input = request.form.get("stock", "").strip()
            uploaded_image = request.files.get("image")
            has_upload = bool(uploaded_image and uploaded_image.filename)
            image_url = product["image_url"] if product else ""
            error_message = None
            try:
                price = Decimal(price_input)
                stock = int(stock_input)
                if not name or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
                    raise ValueError
                if price <= 0 or stock < 0 or not category or len(category) > 50:
                    raise ValueError
            except (InvalidOperation, ValueError):
                error_message = "Suriin ang pangalan, slug, category, presyo, at stock."

            if not error_message and has_upload:
                extension = Path(uploaded_image.filename).suffix.lower()
                header = uploaded_image.stream.read(12)
                uploaded_image.stream.seek(0)
                valid_image = (
                    extension in {".jpg", ".jpeg"} and header.startswith(b"\xff\xd8\xff")
                ) or (
                    extension == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n")
                ) or (
                    extension == ".webp"
                    and header[:4] == b"RIFF"
                    and header[8:12] == b"WEBP"
                )
                if not valid_image:
                    error_message = "Pumili ng valid na JPG, PNG, o WebP na larawan."
            elif not error_message and not image_url:
                error_message = "Mag-upload ng larawan para sa produktong ito."

            if error_message:
                flash(error_message, "error")
            else:
                if has_upload:
                    upload_folder = Path(app.config["PRODUCT_UPLOAD_FOLDER"])
                    upload_folder.mkdir(parents=True, exist_ok=True)
                    filename = f"{uuid4().hex}{extension}"
                    uploaded_image.save(upload_folder / filename)
                    image_url = url_for(
                        "static",
                        filename=f"images/products/{filename}",
                    )

                values = (
                    name,
                    slug,
                    description,
                    category,
                    price,
                    image_url,
                    stock,
                    bool(request.form.get("featured")),
                )
                if product_id:
                    db().execute(
                        "UPDATE products SET name=%s, slug=%s, description=%s, category=%s, "
                        "price=%s, image_url=%s, stock=%s, featured=%s WHERE id=%s",
                        (*values, product_id),
                    )
                else:
                    db().execute(
                        "INSERT INTO products (name, slug, description, category, price, image_url, "
                        "stock, featured) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        values,
                    )
                flash("Na-save ang produkto.", "success")
                return redirect(url_for("admin_products"))
        return render_template("product_form.html", product=product)

    @app.post("/admin/products/<int:product_id>/delete")
    @admin_required
    def delete_product(product_id):
        db().execute("UPDATE products SET active = FALSE WHERE id = %s", (product_id,))
        flash("Inalis ang produkto sa catalog.", "success")
        return redirect(url_for("admin_products"))

    @app.cli.command("seed")
    def seed_products():
        """Replace the sample catalog with the three Gameplan T-shirts."""
        ensure_database()
        db().execute("UPDATE products SET active = FALSE WHERE active = TRUE")
        examples = [
            ("Poster Graphic Tee", "poster-graphic-tee", "Black cotton T-shirt with a bold illustrated front graphic. A standout everyday streetwear piece.", "T-Shirts", 600, "/static/img/poster-graphic-tee.png", 24, True),
            ("Gameplan Blue Logo Tee", "gameplan-blue-logo-tee", "Royal blue cotton T-shirt with red GAMEPLAN lettering across the chest. Comfortable and easy to style.", "T-Shirts", 600, "/static/img/blue-logo-tee.png", 24, True),
            ("All In Statement Tee", "all-in-statement-tee", "Black cotton T-shirt with the motivational To be ballin, you gotta be all-in print. Made for the everyday grind.", "T-Shirts", 600, "/static/img/all-in-tee.png", 24, True),
        ]
        for row in examples:
            db().execute(
                "INSERT INTO products (name, slug, description, category, price, image_url, stock, featured) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description), "
                "category=VALUES(category), price=VALUES(price), image_url=VALUES(image_url), "
                "stock=VALUES(stock), featured=VALUES(featured), active=TRUE",
                row,
            )
        click.echo("The three Gameplan T-shirts are ready.")

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("error.html", code=403, message="Wala kang pahintulot dito."), 403

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", code=404, message="Hindi makita ang pahinang hinahanap mo."), 404

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=app.config.get("DEBUG", False))
