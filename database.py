from contextlib import contextmanager
from pathlib import Path

import mysql.connector

try:
    from mysql.connector.pooling import MySQLConnectionPool
except ImportError:
    MySQLConnectionPool = None


class Database:
    def __init__(self, config):
        self._config = config
        self._pool = None

    def initialize(self):
        try:
            with self.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SHOW TABLES")
                    existing_tables = {row[0] for row in cursor.fetchall()}
                    required_tables = {"users", "products", "orders", "order_items"}
                    if required_tables.issubset(existing_tables):
                        self._ensure_order_item_size(cursor)
                        self._ensure_otp_verifications_table(cursor)
                        return self
        except mysql.connector.Error as exc:
            if exc.errno != 1049:
                raise

        root_config = {
            "host": self._config["MYSQL_HOST"],
            "port": self._config["MYSQL_PORT"],
            "user": self._config["MYSQL_USER"],
            "password": self._config["MYSQL_PASSWORD"],
            "autocommit": True,
        }

        with mysql.connector.connect(**root_config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self._config['MYSQL_DATABASE']}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
                cursor.execute(f"USE `{self._config['MYSQL_DATABASE']}`")
                for statement in self._schema_statements():
                    if statement.strip():
                        cursor.execute(statement)
                self._ensure_order_item_size(cursor)
                self._ensure_otp_verifications_table(cursor)

        return self

    @staticmethod
    def _ensure_order_item_size(cursor):
        cursor.execute("SHOW COLUMNS FROM order_items LIKE 'size'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE order_items ADD COLUMN size VARCHAR(10) NULL AFTER product_name")

    @staticmethod
    def _ensure_otp_verifications_table(cursor):
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS otp_verifications ("
            "email VARCHAR(254) NOT NULL, "
            "name VARCHAR(100) NOT NULL, "
            "password_hash VARCHAR(255) NOT NULL, "
            "role ENUM('customer', 'admin') NOT NULL DEFAULT 'customer', "
            "otp_hash CHAR(64) NOT NULL, "
            "expires_at DATETIME NOT NULL, "
            "created_at DATETIME NOT NULL, "
            "attempts TINYINT UNSIGNED NOT NULL DEFAULT 0, "
            "PRIMARY KEY (email), "
            "KEY ix_otp_verifications_expires (expires_at)"
            ") ENGINE=InnoDB"
        )

    @staticmethod
    def _schema_statements():
        schema_path = Path(__file__).with_name("schema.sql")
        script = schema_path.read_text(encoding="utf-8")
        statements = []
        buffer = ""
        for raw_line in script.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("--"):
                continue
            buffer += raw_line + "\n"
            if line.endswith(";"):
                statement = buffer.strip()
                if statement:
                    statements.append(statement)
                buffer = ""
        if buffer.strip():
            statements.append(buffer.strip())
        return [statement for statement in statements if statement]

    @property
    def pool(self):
        if self._pool is None:
            if MySQLConnectionPool is None:
                raise RuntimeError(
                    "mysql-connector-python is required. Install it with: pip install mysql-connector-python"
                )
            db_password = self._config["MYSQL_PASSWORD"]
            self._pool = MySQLConnectionPool(
                pool_name="gameplan_pool",
                pool_size=5,
                host=self._config["MYSQL_HOST"],
                port=self._config["MYSQL_PORT"],
                user=self._config["MYSQL_USER"],
                password=db_password,
                database=self._config["MYSQL_DATABASE"],
                autocommit=False,
            )
        return self._pool

    @contextmanager
    def connection(self):
        connection = self.pool.get_connection()
        try:
            yield connection
        finally:
            connection.close()

    def query(self, sql, params=(), one=False):
        with self.connection() as connection:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(sql, params)
                result = cursor.fetchone() if one else cursor.fetchall()
                connection.commit()
                return result
            finally:
                cursor.close()

    def execute(self, sql, params=()):
        with self.connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(sql, params)
                connection.commit()
                return cursor.lastrowid, cursor.rowcount
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()

    @contextmanager
    def transaction(self):
        with self.connection() as connection:
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
