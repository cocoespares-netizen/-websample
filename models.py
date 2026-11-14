from dataclasses import dataclass

from flask import current_app
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash


@dataclass
class Product:
    id: int
    name: str
    slug: str
    description: str
    category: str
    price: float
    image_url: str
    stock: int
    featured: bool = False


class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]
        self.name = row["name"]
        self.email = row["email"]
        self.password_hash = row["password_hash"]
        self.role = row["role"]

    @classmethod
    def get(cls, db, user_id):
        row = db.query(
            "SELECT id, name, email, password_hash, role FROM users WHERE id = %s",
            (user_id,),
            one=True,
        )
        return cls(row) if row else None

    @classmethod
    def by_email(cls, db, email):
        row = db.query(
            "SELECT id, name, email, password_hash, role FROM users WHERE email = %s",
            (email,),
            one=True,
        )
        return cls(row) if row else None

    @staticmethod
    def hash_password(password):
        return generate_password_hash(password)

    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        admin_email = current_app.config.get("ADMIN_EMAIL", "").strip().lower()
        return self.role == "admin" or (admin_email and self.email.lower() == admin_email)


class Catalog:
    def __init__(self, db):
        self.db = db

    def list(self, category=None, search=None, min_price=None, max_price=None, sort=None, color=None, size=None):
        conditions = ["active = TRUE"]
        params = []
        if category and category != "All Products":
            conditions.append("category = %s")
            params.append(category)
        if size:
            conditions.append("category = %s")
            params.append("T-Shirts")
        if search:
            conditions.append("(name LIKE %s OR description LIKE %s)")
            params.extend((f"%{search}%", f"%{search}%"))
        if min_price is not None:
            conditions.append("price >= %s")
            params.append(min_price)
        if max_price is not None:
            conditions.append("price <= %s")
            params.append(max_price)
        color_patterns = {
            "black": ("black",),
            "blue": ("blue",),
        }
        if color in color_patterns:
            conditions.append("(name LIKE %s OR description LIKE %s)")
            params.extend(
                f"%{term}%"
                for term in (color_patterns[color][0], color_patterns[color][0])
            )
        ordering = {
            "newest": "created_at DESC",
            "price-low": "price ASC, created_at DESC",
            "price-high": "price DESC, created_at DESC",
        }.get(sort, "featured DESC, created_at DESC")
        rows = self.db.query(
            "SELECT id, name, slug, description, category, price, image_url, stock, featured "
            "FROM products WHERE "
            + " AND ".join(conditions)
            + " ORDER BY "
            + ordering,
            tuple(params),
        )
        return [Product(**row) for row in rows]

    def get(self, product_id):
        row = self.db.query(
            "SELECT id, name, slug, description, category, price, image_url, stock, featured "
            "FROM products WHERE id = %s AND active = TRUE",
            (product_id,),
            one=True,
        )
        return Product(**row) if row else None

    def categories(self):
        rows = self.db.query(
            "SELECT DISTINCT category FROM products WHERE active = TRUE ORDER BY category"
        )
        return [row["category"] for row in rows]
