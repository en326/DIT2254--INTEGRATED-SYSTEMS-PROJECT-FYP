from flask import Flask, render_template, request, redirect, url_for, jsonify, session
import os
import hashlib
import secrets
import smtplib
import ssl
from email.message import EmailMessage
from datetime import datetime, timedelta
import mysql.connector
from mysql.connector import Error
from werkzeug.security import check_password_hash, generate_password_hash

# =====================================
# 1. APPLICATION SETUP
# =====================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_local_env():
    """Load project-local .env settings without requiring an extra package."""
    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.isfile(env_path):
        return

    with open(env_path, encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and value and not os.environ.get(key):
                # Keep an explicit non-empty shell setting; fill missing/empty values from .env.
                os.environ[key] = value


load_local_env()

MAIL_ENV_KEYS = (
    "MUACHEE_SMTP_HOST",
    "MUACHEE_SMTP_PORT",
    "MUACHEE_SMTP_USERNAME",
    "MUACHEE_SMTP_PASSWORD",
)
print("PASSWORD RESET MAIL CONFIG READY:", all(os.environ.get(key) for key in MAIL_ENV_KEYS))

IMAGE_FOLDER = os.path.join(BASE_DIR, "static", "images")

print("APP LOCATION:", BASE_DIR)
print("IMAGE FOLDER:", IMAGE_FOLDER)
print("FOLDER EXISTS:", os.path.isdir(IMAGE_FOLDER))

if os.path.isdir(IMAGE_FOLDER):
    print("IMAGE FILES:", os.listdir(IMAGE_FOLDER))

app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY", "muah-chee-fyp-development-key")


@app.before_request
def require_admin_login():
    """Require a database-authenticated admin for admin pages."""
    allowed_endpoints = {
        "admin_login",
        "admin_forgot_password",
        "admin_reset_confirmation",
        "admin_reset_password",
        "test_db",
        "static",
    }
    if request.endpoint in allowed_endpoints:
        return None
    if request.path.startswith("/admin-") and not session.get("admin_user_id"):
        return redirect(url_for("admin_login"))

# =====================================
# MYSQL DATABASE CONNECTION
# =====================================

# Keep database credentials outside the source code. Set these variables in
# the local .env file or in the hosting provider's environment settings.
DB_CONFIG = {
    "host": os.environ.get("MYSQL_HOST", "localhost"),
    "user": os.environ.get("MYSQL_USER", "root"),
    "password": os.environ.get("MYSQL_PASSWORD", ""),
    "database": os.environ.get("MYSQL_DATABASE", "muah_chee_db"),
    "port": int(os.environ.get("MYSQL_PORT", "3306")),
}

# Aiven requires TLS. Set MYSQL_SSL_CA to the CA certificate file path in
# Render (or locally) to enable certificate and hostname verification.
mysql_ssl_ca = os.environ.get("MYSQL_SSL_CA")
if mysql_ssl_ca:
    DB_CONFIG.update({
        "ssl_ca": mysql_ssl_ca,
        "ssl_verify_cert": True,
        "ssl_verify_identity": True,
    })


def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)


def get_product_settings_from_db():

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        products = {
            "small_price": 3.50,
            "large_price": 5.00,
            "small_available": False,
            "large_available": False,
            "fried_shallots_available": False,
            "black_sesame_available": False,
            "product_available": True
        }

        # Read product prices and availability
        cursor.execute("SELECT * FROM products")
        product_rows = cursor.fetchall()

        for product in product_rows:

            if product["size"].lower() == "small":
                products["small_price"] = float(product["price"])
                products["small_available"] = bool(product["availability"])

            elif product["size"].lower() == "large":
                products["large_price"] = float(product["price"])
                products["large_available"] = bool(product["availability"])

        # Read topping availability
        cursor.execute("SELECT * FROM toppings")
        topping_rows = cursor.fetchall()

        for topping in topping_rows:

            if topping["topping_name"] == "Fried Shallots":
                products["fried_shallots_available"] = bool(
                    topping["availability"]
                )

            elif topping["topping_name"] == "Black Sesame":
                products["black_sesame_available"] = bool(
                    topping["availability"]
                )

        # Read overall ordering availability
        cursor.execute("""
            SELECT setting_value
            FROM system_settings
            WHERE setting_key = 'product_available'
        """)

        setting_row = cursor.fetchone()

        if setting_row:
            products["product_available"] = (
                setting_row["setting_value"] == "1"
            )

        return products

    finally:
        if cursor:
            cursor.close()

        if conn:
            conn.close()

# =====================================
# 2. TEMPORARY NORMAL ORDER DATA
# =====================================

orders = {
    "MC0001": {
        "items": "Small x2, Large x1",
        "small": 2,
        "large": 1,
        "fried_shallots": "Yes",
        "black_sesame": "Yes",
        "total": 12.00,
        "payment_status": "Paid",
        "payment_method": "Touch 'n Go eWallet",
        "order_status": "Preparing",
        "order_type": "Normal Order",
        "customer_name": "Walk-in Customer",
        "phone_number": "Not Provided",
        "order_date": datetime.now().strftime("%Y-%m-%d")
    },

    "MC0002": {
        "items": "Small x2, Large x2",
        "small": 2,
        "large": 2,
        "fried_shallots": "Yes",
        "black_sesame": "Yes",
        "total": 17.00,
        "payment_status": "Paid",
        "payment_method": "Cash",
        "order_status": "Completed",
        "order_type": "Normal Order",
        "customer_name": "Walk-in Customer",
        "phone_number": "Not Provided",
        "order_date": datetime.now().strftime("%Y-%m-%d")
    },

    "MC0003": {
        "items": "Small x3",
        "small": 3,
        "large": 0,
        "fried_shallots": "No",
        "black_sesame": "Yes",
        "total": 10.50,
        "payment_status": "Pending",
        "payment_method": "Cash",
        "order_status": "Pending",
        "order_type": "Normal Order",
        "customer_name": "Walk-in Customer",
        "phone_number": "Not Provided",
        "order_date": datetime.now().strftime("%Y-%m-%d")
    }
}


# =====================================
# 3. TEMPORARY EVENT ORDER DATA
# =====================================

event_orders = {
    "EV0001": {
        "customer_name": "Ng Hong Jie",
        "phone_number": "0149055326",
        "event_date": "2026-12-12",
        "event_address": "Georgetown, Penang",
        "small": 20,
        "large": 30,
        "total_portions": 50,
        "total": 450.00,
        "payment_method": "Touch 'n Go eWallet",
        "payment_status": "Paid",
        "order_status": "Preparing",
        "order_type": "Event Order",
        "order_date": datetime.now().strftime("%Y-%m-%d")
    }
}


# =====================================
# 4. CUSTOMER ROUTES
# =====================================

@app.route("/")
def home():
    return render_template("index.html")



@app.route("/normal-order")
def normal_order():

    try:
        products = get_product_settings_from_db()

        return render_template(
            "normal_order.html",
            products=products
        )

    except Exception as e:
        return f"Normal Order Error: {str(e)}", 500




@app.route("/review-order", methods=["GET", "POST"])
def review_order():

    # Receive customer's selected items
    if request.method == "POST":

        try:
            data = request.get_json()

            if not data:
                return jsonify({
                    "success": False,
                    "message": "No order data received."
                }), 400

            # Read latest settings from MySQL
            products = get_product_settings_from_db()

            if not products["product_available"]:
                return jsonify({
                    "success": False,
                    "message": "Ordering is currently unavailable."
                }), 400

            # Validate quantities
            small_qty = int(data.get("smallQty", 0))
            large_qty = int(data.get("largeQty", 0))

            if small_qty < 0 or large_qty < 0:
                raise ValueError("Invalid quantity.")

            if small_qty > 100 or large_qty > 100:
                raise ValueError("Maximum quantity is 100 per size.")

            if small_qty == 0 and large_qty == 0:
                raise ValueError("Please select at least one product.")

            # Check product availability
            if small_qty > 0 and not products["small_available"]:
                raise ValueError("Small Muah Chee is unavailable.")

            if large_qty > 0 and not products["large_available"]:
                raise ValueError("Large Muah Chee is unavailable.")

            # Read topping selections
            small_shallots = bool(data.get("smallShallots", False))
            small_sesame = bool(data.get("smallSesame", False))
            large_shallots = bool(data.get("largeShallots", False))
            large_sesame = bool(data.get("largeSesame", False))

            # Validate topping availability
            if (
                (small_shallots or large_shallots)
                and not products["fried_shallots_available"]
            ):
                raise ValueError("Fried Shallots is unavailable.")

            if (
                (small_sesame or large_sesame)
                and not products["black_sesame_available"]
            ):
                raise ValueError("Black Sesame is unavailable.")

            # Calculate using database prices
            small_price = products["small_price"]
            large_price = products["large_price"]

            small_subtotal = small_qty * small_price
            large_subtotal = large_qty * large_price

            total = small_subtotal + large_subtotal

            # Save verified order in Flask session
            verified_order = {
                "smallQty": small_qty,
                "largeQty": large_qty,
                "smallShallots": small_shallots,
                "smallSesame": small_sesame,
                "largeShallots": large_shallots,
                "largeSesame": large_sesame,
                "smallPrice": small_price,
                "largePrice": large_price,
                "smallSubtotal": small_subtotal,
                "largeSubtotal": large_subtotal,
                "total": total
            }

            session["normal_order"] = verified_order

            return jsonify({
                "success": True,
                "message": "Order verified successfully."
            })

        except (ValueError, TypeError) as e:
            return jsonify({
                "success": False,
                "message": str(e)
            }), 400

        except Exception as e:
            return jsonify({
                "success": False,
                "message": f"Order verification failed: {str(e)}"
            }), 500

    # Display verified order
    order = session.get("normal_order")

    if not order:
        return redirect(url_for("normal_order"))

    return render_template(
        "review_order.html",
        order=order
    )



@app.route("/payment")
def payment():

    order = session.get("normal_order")

    if not order:
        return redirect(url_for("normal_order"))

    return render_template(
        "payment.html",
        order=order
    )


@app.route("/confirm-normal-payment", methods=["POST"])
def confirm_normal_payment():

    conn = None
    cursor = None

    try:
        data = request.get_json(silent=True) or {}

        # Get the verified order from Flask session
        order = session.get("normal_order")

        if not order:
            return jsonify({
                "success": False,
                "message": "Order information not found. Please order again."
            }), 400

        selected_payment = data.get("payment_method")

        if selected_payment not in ["Cash", "TNG"]:
            return jsonify({
                "success": False,
                "message": "Please select a valid payment method."
            }), 400

        payment_method = (
            "Cash" if selected_payment == "Cash"
            else "Touch 'n Go eWallet"
        )

        # Connect to MySQL
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Get product IDs from database
        cursor.execute("""
            SELECT product_id, size
            FROM products
        """)
        product_rows = cursor.fetchall()

        product_ids = {
            row["size"].strip().lower(): row["product_id"]
            for row in product_rows
        }

        # Get topping IDs from database
        cursor.execute("""
            SELECT topping_id, topping_name
            FROM toppings
        """)
        topping_rows = cursor.fetchall()

        topping_ids = {
            row["topping_name"]: row["topping_id"]
            for row in topping_rows
        }

        # Check required products
        if order["smallQty"] > 0 and "small" not in product_ids:
            raise ValueError("Small Muah Chee product is missing in database.")

        if order["largeQty"] > 0 and "large" not in product_ids:
            raise ValueError("Large Muah Chee product is missing in database.")

        # Check required toppings
        required_toppings = []

        if order["smallShallots"] or order["largeShallots"]:
            required_toppings.append("Fried Shallots")

        if order["smallSesame"] or order["largeSesame"]:
            required_toppings.append("Black Sesame")

        for topping_name in required_toppings:
            if topping_name not in topping_ids:
                raise ValueError(
                    f"{topping_name} is missing in database."
                )

        # Generate order number based on the latest order ID
        cursor.execute("""
            SELECT MAX(order_id) AS last_id
            FROM orders
        """)

        last_order = cursor.fetchone()
        next_id = (last_order["last_id"] or 0) + 1
        order_number = f"MC{next_id:04d}"

        # Insert main order
        cursor.execute("""
            INSERT INTO orders
            (
                order_number,
                customer_id,
                user_id,
                order_type,
                order_source,
                order_date,
                order_status,
                total_amount
            )
            VALUES (%s, NULL, NULL, %s, %s, NOW(), %s, %s)
        """, (
            order_number,
            "Normal Order",
            "QR",
            "Pending",
            order["total"]
        ))

        order_id = cursor.lastrowid

        # Insert Small Muah Chee
        if order["smallQty"] > 0:

            cursor.execute("""
                INSERT INTO order_items
                (order_id, product_id, quantity, unit_price, subtotal)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                order_id,
                product_ids["small"],
                order["smallQty"],
                order["smallPrice"],
                order["smallSubtotal"]
            ))

            small_item_id = cursor.lastrowid

            if order["smallShallots"]:
                cursor.execute("""
                    INSERT INTO order_item_toppings
                    (order_item_id, topping_id)
                    VALUES (%s, %s)
                """, (
                    small_item_id,
                    topping_ids["Fried Shallots"]
                ))

            if order["smallSesame"]:
                cursor.execute("""
                    INSERT INTO order_item_toppings
                    (order_item_id, topping_id)
                    VALUES (%s, %s)
                """, (
                    small_item_id,
                    topping_ids["Black Sesame"]
                ))

        # Insert Large Muah Chee
        if order["largeQty"] > 0:

            cursor.execute("""
                INSERT INTO order_items
                (order_id, product_id, quantity, unit_price, subtotal)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                order_id,
                product_ids["large"],
                order["largeQty"],
                order["largePrice"],
                order["largeSubtotal"]
            ))

            large_item_id = cursor.lastrowid

            if order["largeShallots"]:
                cursor.execute("""
                    INSERT INTO order_item_toppings
                    (order_item_id, topping_id)
                    VALUES (%s, %s)
                """, (
                    large_item_id,
                    topping_ids["Fried Shallots"]
                ))

            if order["largeSesame"]:
                cursor.execute("""
                    INSERT INTO order_item_toppings
                    (order_item_id, topping_id)
                    VALUES (%s, %s)
                """, (
                    large_item_id,
                    topping_ids["Black Sesame"]
                ))

        # Insert payment record
        cursor.execute("""
            INSERT INTO payments
            (
                order_id,
                payment_method,
                payment_status,
                amount
            )
            VALUES (%s, %s, %s, %s)
        """, (
            order_id,
            payment_method,
            "Pending",
            order["total"]
        ))

        # Save all database records
        conn.commit()

        # Prepare order information for success page
        completed_order = dict(order)

        completed_order["order_number"] = order_number
        completed_order["payment_method"] = payment_method
        completed_order["payment_status"] = "Pending"

        # Store completed order in Flask session
        session["last_order_number"] = order_number
        session["completed_order"] = completed_order

        # Remove unpaid order from current checkout session
        session.pop("normal_order", None)

        return jsonify({
            "success": True,
            "redirect_url": url_for("order_success")
        })

    except Exception as e:

        if conn:
            conn.rollback()

        print("PAYMENT ERROR:", str(e))

        return jsonify({
            "success": False,
            "message": str(e)
        }), 500

    finally:

        if cursor:
            cursor.close()

        if conn:
            conn.close()



@app.route("/order-success")
def order_success():

    order = session.get("completed_order")

    if not order:
        return redirect(url_for("normal_order"))

    # Prepare product items
    items = []

    # Small Muah Chee
    if int(order.get("smallQty", 0)) > 0:

        small_toppings = []

        if order.get("smallShallots"):
            small_toppings.append("Fried Shallots")

        if order.get("smallSesame"):
            small_toppings.append("Black Sesame")

        items.append({
            "size": "Small",
            "quantity": int(order["smallQty"]),
            "unit_price": float(order["smallPrice"]),
            "subtotal": float(order["smallSubtotal"]),
            "toppings": small_toppings
        })

    # Large Muah Chee
    if int(order.get("largeQty", 0)) > 0:

        large_toppings = []

        if order.get("largeShallots"):
            large_toppings.append("Fried Shallots")

        if order.get("largeSesame"):
            large_toppings.append("Black Sesame")

        items.append({
            "size": "Large",
            "quantity": int(order["largeQty"]),
            "unit_price": float(order["largePrice"]),
            "subtotal": float(order["largeSubtotal"]),
            "toppings": large_toppings
        })

    # Prepare data for HTML
    success_order = {
        "order_number": order.get("order_number"),
        "items": items,
        "total_amount": float(order["total"]),
        "payment_method": order.get("payment_method", "-"),
        "payment_status": order.get("payment_status", "Pending")
    }

    return render_template(
        "order_success.html",
        order=success_order
    )




@app.route("/event-details")
def event_details():
    return render_template("event_details.html")


@app.route("/event-review")
def event_review():
    return render_template("event_review.html")


@app.route("/event-payment")
def event_payment():
    return render_template("event_payment.html")


@app.route("/event-success")
def event_success():
    return render_template("event_success.html")


# =====================================
# 5. ADMIN LOGIN ROUTES
# =====================================

@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if session.get("admin_user_id"):
        return redirect(url_for("admin_dashboard"))

    error = None
    if request.method == "POST":
        username = (
            request.form.get("username")
            or request.form.get("admin_username")
            or ""
        ).strip()
        password = request.form.get("password", "")

        if not username or not password:
            error = "Please enter both username and password."
        else:
            conn = None
            cursor = None
            try:
                conn = get_db_connection()
                cursor = conn.cursor(dictionary=True)
                cursor.execute("""
                    SELECT user_id, username, password_hash, role
                    FROM users
                    WHERE username = %s
                    LIMIT 1
                """, (username,))
                user = cursor.fetchone()

                if (
                    user
                    and user["role"].strip().lower() == "admin"
                    and check_password_hash(user["password_hash"], password)
                ):
                    session.clear()
                    session["admin_user_id"] = user["user_id"]
                    session["admin_username"] = user["username"]
                    return redirect(url_for("admin_dashboard"))

                error = "Invalid admin username or password."
            except Exception:
                app.logger.exception("Admin login database error")
                error = "Unable to verify login. Check the database connection."
            finally:
                if cursor:
                    cursor.close()
                if conn:
                    conn.close()

    return render_template(
        "admin_login.html",
        error=error,
        success=request.args.get("reset") == "1",
    )


@app.route("/admin-signup", methods=["GET", "POST"])
def admin_signup():
    error = None

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(username) < 3 or len(username) > 50:
            error = "Username must be between 3 and 50 characters."
        elif not email or "@" not in email:
            error = "Enter a valid email address."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm_password:
            error = "Passwords do not match."
        else:
            conn = None
            cursor = None
            try:
                conn = get_db_connection()
                cursor = conn.cursor(dictionary=True)
                cursor.execute("""
                    SELECT user_id
                    FROM users
                    WHERE username = %s OR LOWER(email) = %s
                    LIMIT 1
                """, (username, email))
                if cursor.fetchone():
                    error = "That username or email is already in use."
                else:
                    cursor.execute("""
                        INSERT INTO users (username, email, password_hash, role)
                        VALUES (%s, %s, %s, 'admin')
                    """, (username, email, generate_password_hash(password)))
                    conn.commit()
                    return redirect(url_for("admin_signup", created="1"))
            except Exception:
                if conn:
                    conn.rollback()
                app.logger.exception("Admin signup database error")
                error = "Unable to create the account. Check the database connection."
            finally:
                if cursor:
                    cursor.close()
                if conn:
                    conn.close()

    return render_template(
        "admin_signup.html",
        error=error,
        success=request.args.get("created") == "1",
    )


@app.route("/admin-logout")
@app.route("/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))



@app.route("/admin-dashboard")
def admin_dashboard():
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT
                o.order_number AS order_id,
                o.order_type,
                DATE_FORMAT(o.order_date, '%Y-%m-%d') AS order_date,
                o.order_status,
                o.total_amount AS total,
                COALESCE(p.payment_status, 'Pending') AS payment_status,
                COALESCE(GROUP_CONCAT(
                    CONCAT(pr.size, ' x', oi.quantity)
                    ORDER BY oi.order_item_id SEPARATOR ', '
                ), '') AS items
            FROM orders o
            LEFT JOIN payments p ON p.order_id = o.order_id
            LEFT JOIN order_items oi ON oi.order_id = o.order_id
            LEFT JOIN products pr ON pr.product_id = oi.product_id
            GROUP BY o.order_id, o.order_number, o.order_type,
                o.order_date, o.order_status, o.total_amount,
                p.payment_status
            ORDER BY o.order_id DESC
        """)

        all_orders = []
        for row in cursor.fetchall():
            all_orders.append({
                "order_id": row["order_id"],
                "items": row["items"] or "-",
                "total": float(row["total"] or 0),
                "order_status": row["order_status"] or "Pending",
                "payment_status": row["payment_status"] or "Pending",
                "order_date": row["order_date"] or "",
                "order_type": row["order_type"] or "Normal Order"
            })

        today_string = datetime.now().strftime("%Y-%m-%d")
        today_orders = [
            order for order in all_orders
            if order["order_date"] == today_string
        ]
        pending_orders = [
            order for order in today_orders
            if order["order_status"] == "Pending"
        ]
        total_event_orders = sum(
            1 for order in all_orders
            if order["order_type"].strip().lower().startswith("event")
        )
        today_sales = sum(
            order["total"] for order in today_orders
            if order["payment_status"] == "Paid"
        )
        recent_orders = sorted(
            all_orders,
            key=lambda order: order["order_date"],
            reverse=True
        )[:5]

        return render_template(
            "admin_dashboard.html",
            new_orders=len(today_orders),
            pending_orders=len(pending_orders),
            total_event_orders=total_event_orders,
            today_sales=today_sales,
            recent_orders=recent_orders
        )

    except Exception as e:
        if conn:
            conn.rollback()
        return f"Admin Dashboard Error: {str(e)}", 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()



@app.route("/admin-forgot-password", methods=["GET", "POST"])
def admin_forgot_password():
    error = None
    if request.method == "POST":
        # Re-read project settings so the reset route does not depend on which
        # terminal or VS Code environment started the Flask process.
        load_local_env()
        email = request.form.get("email", "").strip().lower()
        smtp_host = os.environ.get("MUACHEE_SMTP_HOST")
        try:
            smtp_port = int(os.environ.get("MUACHEE_SMTP_PORT", "587"))
        except ValueError:
            smtp_port = 0
        smtp_user = os.environ.get("MUACHEE_SMTP_USERNAME")
        smtp_password = os.environ.get("MUACHEE_SMTP_PASSWORD")
        smtp_from = os.environ.get("MUACHEE_SMTP_FROM") or smtp_user

        if not email:
            error = "Enter the email address linked to your admin account."
        elif not 1 <= smtp_port <= 65535:
            error = "The password reset email port is invalid."
        elif not all([smtp_host, smtp_user, smtp_password, smtp_from]):
            missing = [
                name for name, value in (
                    ("MUACHEE_SMTP_HOST", smtp_host),
                    ("MUACHEE_SMTP_USERNAME", smtp_user),
                    ("MUACHEE_SMTP_PASSWORD", smtp_password),
                    ("MUACHEE_SMTP_FROM", smtp_from),
                ) if not value
            ]
            app.logger.error("Missing password reset mail settings: %s", ", ".join(missing))
            error = "Email setup is missing: " + ", ".join(missing)
        else:
            conn = None
            cursor = None
            user_id = None
            raw_token = None
            try:
                conn = get_db_connection()
                cursor = conn.cursor(dictionary=True)
                cursor.execute("""
                    SELECT user_id
                    FROM users
                    WHERE LOWER(email) = %s AND LOWER(role) = 'admin'
                    LIMIT 1
                """, (email,))
                user = cursor.fetchone()

                if user:
                    user_id = user["user_id"]
                    raw_token = secrets.token_urlsafe(32)
                    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
                    expires_at = datetime.now() + timedelta(minutes=30)

                    cursor.execute(
                        "DELETE FROM password_reset_tokens WHERE user_id = %s",
                        (user_id,),
                    )
                    cursor.execute("""
                        INSERT INTO password_reset_tokens
                            (user_id, token_hash, expires_at)
                        VALUES (%s, %s, %s)
                    """, (user_id, token_hash, expires_at))
                    conn.commit()

                    reset_link = url_for(
                        "admin_reset_password",
                        token=raw_token,
                        _external=True,
                    )
                    message = EmailMessage()
                    message["Subject"] = "Muah Chee Admin Password Reset"
                    message["From"] = smtp_from
                    message["To"] = email
                    message.set_content(
                        "A password reset was requested for your admin account.\n\n"
                        f"Open this link within 30 minutes to set a new password:\n{reset_link}\n\n"
                        "If you did not request this, you can ignore this email."
                    )

                    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
                        server.starttls(context=ssl.create_default_context())
                        server.login(smtp_user, smtp_password)
                        server.send_message(message)

                # Keep the response identical for known and unknown email addresses.
                return redirect(url_for("admin_reset_confirmation"))
            except Exception:
                if conn:
                    conn.rollback()
                app.logger.exception("Admin password reset email failed")
                if user_id and raw_token and conn:
                    cleanup_cursor = None
                    try:
                        cleanup_cursor = conn.cursor()
                        cleanup_cursor.execute(
                            "DELETE FROM password_reset_tokens WHERE user_id = %s",
                            (user_id,),
                        )
                        conn.commit()
                    except Exception:
                        conn.rollback()
                    finally:
                        if cleanup_cursor:
                            cleanup_cursor.close()
                error = "Could not send the reset email. Check the email settings and try again."
            finally:
                if cursor:
                    cursor.close()
                if conn:
                    conn.close()

    return render_template("admin_forgot_password.html", error=error)


@app.route("/admin-reset-confirmation")
def admin_reset_confirmation():
    return render_template("admin_reset_confirmation.html")


@app.route("/admin-reset-password/<token>", methods=["GET", "POST"])
def admin_reset_password(token):
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    error = None

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        if len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm_password:
            error = "Passwords do not match."
        else:
            conn = None
            cursor = None
            try:
                conn = get_db_connection()
                cursor = conn.cursor(dictionary=True)
                cursor.execute("""
                    SELECT token_hash, user_id
                    FROM password_reset_tokens
                    WHERE token_hash = %s
                      AND expires_at > NOW()
                      AND used_at IS NULL
                    LIMIT 1
                    FOR UPDATE
                """, (token_hash,))
                reset_record = cursor.fetchone()

                if not reset_record:
                    error = "This reset link is invalid or expired. Request a new one."
                else:
                    cursor.execute(
                        "UPDATE users SET password_hash = %s WHERE user_id = %s",
                        (generate_password_hash(password), reset_record["user_id"]),
                    )
                    cursor.execute("""
                        UPDATE password_reset_tokens
                        SET used_at = NOW()
                        WHERE token_hash = %s AND used_at IS NULL
                    """, (token_hash,))
                    cursor.execute("""
                        DELETE FROM password_reset_tokens
                        WHERE user_id = %s AND token_hash <> %s
                    """, (reset_record["user_id"], token_hash))
                    conn.commit()
                    return redirect(url_for("admin_login", reset="1"))
            except Exception:
                if conn:
                    conn.rollback()
                app.logger.exception("Admin password reset failed")
                error = "Unable to reset the password. Please request a new link."
            finally:
                if cursor:
                    cursor.close()
                if conn:
                    conn.close()

    return render_template("admin_reset_password.html", error=error)


# =====================================
# 6. NORMAL ORDER MANAGEMENT
# =====================================


@app.route("/admin-order-management")
def admin_order_management():

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Get normal customer orders from MySQL
        cursor.execute("""
            SELECT
                o.order_id,
                o.order_number,
                o.order_type,
                c.customer_name,
                c.phone_number,
                o.order_date,
                o.order_status,
                o.total_amount,
                p.payment_method,
                p.payment_status,
                ed.contact_name AS event_contact_name,
                ed.contact_phone AS event_contact_phone
            FROM orders o
            LEFT JOIN payments p
                ON o.order_id = p.order_id
            LEFT JOIN customers c
                ON o.customer_id = c.customer_id
            LEFT JOIN event_details ed
                ON o.order_id = ed.order_id
            ORDER BY o.order_id DESC
        """)

        db_orders = cursor.fetchall()

        orders_for_page = {}

        for order in db_orders:

            # Get products for this order
            cursor.execute("""
                SELECT
                    pr.size,
                    oi.quantity
                FROM order_items oi
                JOIN products pr
                    ON oi.product_id = pr.product_id
                WHERE oi.order_id = %s
            """, (order["order_id"],))

            item_rows = cursor.fetchall()

            item_names = []

            for item in item_rows:
                item_names.append(
                    f'{item["size"]} x{item["quantity"]}'
                )

            order_number = order["order_number"]

            orders_for_page[order_number] = {
                "items": ", ".join(item_names),
                "total": float(order["total_amount"] or 0),
                "order_status": order["order_status"] or "Pending",
                "payment_status": order["payment_status"] or "Pending",
                "payment_method": order["payment_method"] or "-",
                "order_type": order["order_type"] or "Normal Order",
                "order_date": order["order_date"]
            }

        return render_template(
            "admin_order_management.html",
            orders=orders_for_page
        )

    except Exception as e:
        print("ADMIN ORDER MANAGEMENT ERROR:", str(e))
        return "Unable to load orders. Please check the database.", 500

    finally:
        if cursor:
            cursor.close()

        if conn:
            conn.close()



# =====================================
# 7. NORMAL ORDER DETAILS
# =====================================


@app.route("/admin-order-details/<order_id>", methods=["GET", "POST"])
def admin_order_details(order_id):

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Find order using order number, e.g. MC0010
        cursor.execute("""
            SELECT
                o.order_id,
                o.order_number,
                o.order_type,
                c.customer_name,
                c.phone_number,
                o.order_date,
                o.order_status,
                o.total_amount,
                p.payment_method,
                p.payment_status,
                ed.contact_name AS event_contact_name,
                ed.contact_phone AS event_contact_phone
            FROM orders o
            LEFT JOIN payments p
                ON o.order_id = p.order_id
            LEFT JOIN customers c
                ON o.customer_id = c.customer_id
            LEFT JOIN event_details ed
                ON o.order_id = ed.order_id
            WHERE o.order_number = %s
            LIMIT 1
        """, (order_id,))

        order = cursor.fetchone()

        if not order:
            return "Order not found", 404

        # Update order status
        if request.method == "POST":

            new_status = request.form.get("status")

            if new_status not in ["Pending", "Preparing", "Completed"]:
                return "Invalid order status", 400

            cursor.execute("""
                UPDATE orders
                SET order_status = %s
                WHERE order_id = %s
            """, (
                new_status,
                order["order_id"]
            ))

            conn.commit()

            return redirect(url_for(
                "admin_order_details",
                order_id=order_id
            ))

        # Get ordered products
        cursor.execute("""
            SELECT
                pr.size,
                oi.quantity,
                oi.unit_price,
                oi.subtotal,
                oi.order_item_id
            FROM order_items oi
            JOIN products pr
                ON oi.product_id = pr.product_id
            WHERE oi.order_id = %s
        """, (order["order_id"],))

        item_rows = cursor.fetchall()

        # Prepare details in the format used by your HTML
        small_qty = 0
        large_qty = 0
        small_subtotal = 0.0
        large_subtotal = 0.0
        fried_shallots = "No"
        black_sesame = "No"

        for item in item_rows:

            if item["size"].strip().lower() == "small":
                small_qty += int(item["quantity"])
                small_subtotal += float(item["subtotal"] or 0)

            elif item["size"].strip().lower() == "large":
                large_qty += int(item["quantity"])
                large_subtotal += float(item["subtotal"] or 0)

            # Get toppings for this item
            cursor.execute("""
                SELECT t.topping_name
                FROM order_item_toppings oit
                JOIN toppings t
                    ON oit.topping_id = t.topping_id
                WHERE oit.order_item_id = %s
            """, (item["order_item_id"],))

            topping_rows = cursor.fetchall()

            for topping in topping_rows:

                if topping["topping_name"] == "Fried Shallots":
                    fried_shallots = "Yes"

                if topping["topping_name"] == "Black Sesame":
                    black_sesame = "Yes"

        order_type = order["order_type"] or "Normal Order"
        is_event_order = order_type.strip().lower().startswith("event")

        if is_event_order:
            customer_name = order.get("event_contact_name") or "Not Provided"
            phone_number = order.get("event_contact_phone") or "Not Provided"
        elif order_type.strip().lower().startswith("walk-in"):
            customer_name = order.get("customer_name") or "Walk-in Customer"
            phone_number = order.get("phone_number") or "Not Provided"
        elif order.get("customer_name") or order.get("phone_number"):
            customer_name = order.get("customer_name") or "Not Provided"
            phone_number = order.get("phone_number") or "Not Provided"
        else:
            customer_name = "QR Customer"
            phone_number = "Not Provided"

        order_details = {
            "order_type": order_type,
            "customer_name": customer_name,
            "phone_number": phone_number,
            "small": small_qty,
            "large": large_qty,
            "small_subtotal": small_subtotal,
            "large_subtotal": large_subtotal,
            "fried_shallots": fried_shallots,
            "black_sesame": black_sesame,
            "total": float(order["total_amount"] or 0),
            "payment_status": order["payment_status"] or "Pending",
            "payment_method": order["payment_method"] or "-",
            "order_status": order["order_status"] or "Pending"
        }

        return render_template(
            "admin_order_details.html",
            order_id=order["order_number"],
            order=order_details
        )

    except Exception as e:

        if conn:
            conn.rollback()

        print("ADMIN ORDER DETAILS ERROR:", str(e))

        return f"Order Details Error: {str(e)}", 500

    finally:

        if cursor:
            cursor.close()

        if conn:
            conn.close()


# =====================================
# 8. ADD MANUAL WALK-IN ORDER
# =====================================

@app.route("/admin-manual-order", methods=["GET", "POST"])
def admin_manual_order():

    if request.method == "POST":

        customer_name = request.form.get(
            "customer_name", ""
        ).strip()

        phone_number = request.form.get(
            "phone_number", ""
        ).strip()

        try:
            small = int(request.form.get("small", 0))
            large = int(request.form.get("large", 0))
        except (ValueError, TypeError):
            return "Invalid quantity", 400

        if small < 0 or large < 0 or (small + large) == 0:
            return "Please enter a valid order quantity", 400

        fried_shallots_selected = bool(request.form.get("fried_shallots"))
        black_sesame_selected = bool(request.form.get("black_sesame"))

        payment_method = request.form.get("payment_method")

        if payment_method not in ["Cash", "Touch 'n Go eWallet"]:
            return "Invalid payment method", 400

        try:
            products = get_product_settings_from_db()
        except Exception as e:
            return f"Unable to read product settings: {str(e)}", 500

        if not products["product_available"]:
            return "Ordering is currently unavailable", 400

        if small > 0 and not products["small_available"]:
            return "Small Muah Chee is currently unavailable", 400

        if large > 0 and not products["large_available"]:
            return "Large Muah Chee is currently unavailable", 400

        if fried_shallots_selected and not products["fried_shallots_available"]:
            return "Fried Shallots is currently unavailable", 400
        if black_sesame_selected and not products["black_sesame_available"]:
            return "Black Sesame is currently unavailable", 400

        conn = None
        cursor = None

        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)

            cursor.execute("SELECT product_id, size FROM products")
            product_ids = {
                row["size"].strip().lower(): row["product_id"]
                for row in cursor.fetchall()
            }
            cursor.execute("SELECT topping_id, topping_name FROM toppings")
            topping_ids = {
                row["topping_name"]: row["topping_id"]
                for row in cursor.fetchall()
            }

            if small > 0 and "small" not in product_ids:
                raise ValueError("Small Muah Chee product is missing in database.")
            if large > 0 and "large" not in product_ids:
                raise ValueError("Large Muah Chee product is missing in database.")
            if fried_shallots_selected and "Fried Shallots" not in topping_ids:
                raise ValueError("Fried Shallots topping is missing in database.")
            if black_sesame_selected and "Black Sesame" not in topping_ids:
                raise ValueError("Black Sesame topping is missing in database.")

            small_price = products["small_price"]
            large_price = products["large_price"]
            small_subtotal = small * small_price
            large_subtotal = large * large_price
            total = small_subtotal + large_subtotal

            customer_id = None
            if customer_name or phone_number:
                cursor.execute("""
                    INSERT INTO customers (customer_name, phone_number)
                    VALUES (%s, %s)
                """, (customer_name or None, phone_number or None))
                customer_id = cursor.lastrowid

            cursor.execute("SELECT COALESCE(MAX(order_id), 0) AS last_id FROM orders")
            next_order_number = int(cursor.fetchone()["last_id"] or 0) + 1
            new_order_number = f"MC{next_order_number:04d}"

            cursor.execute("""
                INSERT INTO orders
                    (order_number, customer_id, user_id, order_type,
                     order_source, order_date, order_status, total_amount)
                VALUES (%s, %s, NULL, %s, %s, NOW(), %s, %s)
            """, (
                new_order_number, customer_id, "Walk-in Order",
                "Admin", "Pending", total
            ))
            order_db_id = cursor.lastrowid

            if small > 0:
                cursor.execute("""
                    INSERT INTO order_items
                        (order_id, product_id, quantity, unit_price, subtotal)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    order_db_id, product_ids["small"], small,
                    small_price, small_subtotal
                ))
                small_item_id = cursor.lastrowid
                if fried_shallots_selected:
                    cursor.execute("""
                        INSERT INTO order_item_toppings
                            (order_item_id, topping_id)
                        VALUES (%s, %s)
                    """, (small_item_id, topping_ids["Fried Shallots"]))
                if black_sesame_selected:
                    cursor.execute("""
                        INSERT INTO order_item_toppings
                            (order_item_id, topping_id)
                        VALUES (%s, %s)
                    """, (small_item_id, topping_ids["Black Sesame"]))

            if large > 0:
                cursor.execute("""
                    INSERT INTO order_items
                        (order_id, product_id, quantity, unit_price, subtotal)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    order_db_id, product_ids["large"], large,
                    large_price, large_subtotal
                ))
                large_item_id = cursor.lastrowid
                if fried_shallots_selected:
                    cursor.execute("""
                        INSERT INTO order_item_toppings
                            (order_item_id, topping_id)
                        VALUES (%s, %s)
                    """, (large_item_id, topping_ids["Fried Shallots"]))
                if black_sesame_selected:
                    cursor.execute("""
                        INSERT INTO order_item_toppings
                            (order_item_id, topping_id)
                        VALUES (%s, %s)
                    """, (large_item_id, topping_ids["Black Sesame"]))

            cursor.execute("""
                INSERT INTO payments
                    (order_id, payment_method, payment_status, amount)
                VALUES (%s, %s, %s, %s)
            """, (
                order_db_id, payment_method,
                "Paid" if payment_method == "Cash" else "Pending",
                total
            ))

            conn.commit()
            return redirect(url_for(
                "admin_order_details",
                order_id=new_order_number
            ))

        except ValueError as e:
            if conn:
                conn.rollback()
            return str(e), 400
        except Exception as e:
            if conn:
                conn.rollback()
            return f"Add Walk-in Order Error: {str(e)}", 500
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    return render_template("admin_manual_order.html")


# =====================================
# 9. EVENT ORDER LIST
# =====================================

@app.route("/admin-events")
def admin_events():
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT
                o.order_number,
                o.order_type,
                o.order_date,
                o.order_status,
                o.total_amount,
                ed.contact_name,
                ed.contact_phone,
                ed.event_date,
                ed.event_address,
                ed.delivery_method,
                p.payment_method,
                p.payment_status,
                COALESCE(SUM(CASE WHEN LOWER(pr.size) = 'small'
                    THEN oi.quantity ELSE 0 END), 0) AS small,
                COALESCE(SUM(CASE WHEN LOWER(pr.size) = 'large'
                    THEN oi.quantity ELSE 0 END), 0) AS large
            FROM orders o
            JOIN event_details ed ON ed.order_id = o.order_id
            LEFT JOIN payments p ON p.order_id = o.order_id
            LEFT JOIN order_items oi ON oi.order_id = o.order_id
            LEFT JOIN products pr ON pr.product_id = oi.product_id
            WHERE LOWER(o.order_type) LIKE 'event%'
            GROUP BY o.order_id, o.order_number, o.order_type,
                o.order_date, o.order_status, o.total_amount,
                ed.contact_name, ed.contact_phone, ed.event_date,
                ed.event_address, ed.delivery_method,
                p.payment_method, p.payment_status
            ORDER BY o.order_id DESC
        """)

        event_rows = cursor.fetchall()
        event_orders_from_db = {}

        for row in event_rows:
            event_orders_from_db[row["order_number"]] = {
                "customer_name": row["contact_name"],
                "phone_number": row["contact_phone"],
                "event_date": row["event_date"],
                "event_address": row["event_address"],
                "delivery_method": row["delivery_method"],
                "small": int(row["small"] or 0),
                "large": int(row["large"] or 0),
                "total_portions": int(row["small"] or 0) + int(row["large"] or 0),
                "total": float(row["total_amount"] or 0),
                "payment_method": row["payment_method"] or "-",
                "payment_status": row["payment_status"] or "Pending",
                "order_status": row["order_status"] or "Pending",
                "order_type": row["order_type"],
                "order_date": row["order_date"]
            }

        return render_template(
            "admin_events.html",
            event_orders=event_orders_from_db
        )

    except Exception as e:
        if conn:
            conn.rollback()
        return f"Event List Error: {str(e)}", 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# =====================================
# 10. ADD EVENT ORDER
# =====================================

@app.route("/admin-add-event", methods=["GET", "POST"])
def admin_add_event():

    if request.method == "POST":

        customer_name = request.form.get(
            "customer_name", ""
        ).strip()

        phone_number = request.form.get(
            "phone_number", ""
        ).strip()

        event_date = request.form.get("event_date")
        event_address = request.form.get(
            "event_address", ""
        ).strip()

        payment_method = request.form.get("payment_method")

        try:
            small = int(request.form.get("small", 0))
            large = int(request.form.get("large", 0))
        except (ValueError, TypeError):
            return "Invalid order information", 400

        if not customer_name or not phone_number:
            return "Please enter customer information", 400

        if not event_date or not event_address:
            return "Please enter event information", 400

        if small < 0 or large < 0 or (small + large) == 0:
            return "Please enter valid order quantities", 400

        total = (small * 3.50) + (large * 5.00)

        if payment_method not in ["Cash", "Touch 'n Go eWallet"]:
            return "Invalid payment method", 400

        conn = None
        cursor = None

        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)

            cursor.execute("""
                SELECT COALESCE(MAX(CAST(SUBSTRING_INDEX(order_number, '-', -1)
                    AS UNSIGNED)), 0) AS last_number
                FROM orders
                WHERE order_number LIKE 'MC-EVENT-%'
            """)
            last_row = cursor.fetchone()
            next_number = int(last_row["last_number"] or 0) + 1
            new_event_id = f"MC-EVENT-{next_number:03d}"

            cursor.execute("SELECT product_id, size FROM products")
            product_ids = {
                row["size"].strip().lower(): row["product_id"]
                for row in cursor.fetchall()
            }

            if small > 0 and "small" not in product_ids:
                raise ValueError("Small Muah Chee product is missing in database.")
            if large > 0 and "large" not in product_ids:
                raise ValueError("Large Muah Chee product is missing in database.")

            cursor.execute("""
                INSERT INTO orders
                    (order_number, customer_id, user_id, order_type,
                     order_source, order_date, order_status, total_amount)
                VALUES (%s, NULL, NULL, %s, %s, NOW(), %s, %s)
            """, (
                new_event_id, "Event", "Admin", "Pending", total
            ))
            order_db_id = cursor.lastrowid

            cursor.execute("""
                INSERT INTO event_details
                    (order_id, contact_name, contact_phone, event_date,
                     event_address, delivery_method)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                order_db_id, customer_name, phone_number, event_date,
                event_address, "Delivery"
            ))

            if small > 0:
                cursor.execute("""
                    INSERT INTO order_items
                        (order_id, product_id, quantity, unit_price, subtotal)
                    VALUES (%s, %s, %s, %s, %s)
                """, (order_db_id, product_ids["small"], small, 3.50, small * 3.50))

            if large > 0:
                cursor.execute("""
                    INSERT INTO order_items
                        (order_id, product_id, quantity, unit_price, subtotal)
                    VALUES (%s, %s, %s, %s, %s)
                """, (order_db_id, product_ids["large"], large, 5.00, large * 5.00))

            cursor.execute("""
                INSERT INTO payments
                    (order_id, payment_method, payment_status, amount)
                VALUES (%s, %s, %s, %s)
            """, (
                order_db_id,
                payment_method,
                "Paid" if payment_method == "Cash" else "Pending",
                total
            ))

            conn.commit()
            return redirect(url_for(
                "admin_event_order_details",
                event_id=new_event_id
            ))

        except ValueError as e:
            if conn:
                conn.rollback()
            return str(e), 400
        except Exception as e:
            if conn:
                conn.rollback()
            return f"Add Event Order Error: {str(e)}", 500
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    return render_template("admin_add_event.html")


# =====================================
# 11. EVENT ORDER DETAILS
# =====================================

@app.route(
    "/admin-event-details/<event_id>",
    methods=["GET", "POST"]
)
def admin_event_order_details(event_id):
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        if request.method == "POST":
            new_status = request.form.get("status")
            if new_status not in ["Pending", "Preparing", "Completed"]:
                return "Invalid order status", 400
            cursor.execute("""
                UPDATE orders SET order_status = %s
                WHERE order_number = %s AND LOWER(order_type) LIKE 'event%'
            """, (new_status, event_id))
            conn.commit()
            return redirect(url_for("admin_event_order_details", event_id=event_id))

        cursor.execute("""
            SELECT
                o.order_id, o.order_number, o.order_type, o.order_date,
                o.order_status, o.total_amount,
                ed.contact_name, ed.contact_phone, ed.event_date,
                ed.event_address, ed.delivery_method,
                p.payment_method, p.payment_status,
                COALESCE(SUM(CASE WHEN LOWER(pr.size) = 'small'
                    THEN oi.quantity ELSE 0 END), 0) AS small,
                COALESCE(SUM(CASE WHEN LOWER(pr.size) = 'large'
                    THEN oi.quantity ELSE 0 END), 0) AS large
            FROM orders o
            JOIN event_details ed ON ed.order_id = o.order_id
            LEFT JOIN payments p ON p.order_id = o.order_id
            LEFT JOIN order_items oi ON oi.order_id = o.order_id
            LEFT JOIN products pr ON pr.product_id = oi.product_id
            WHERE o.order_number = %s
            GROUP BY o.order_id, o.order_number, o.order_type, o.order_date,
                o.order_status, o.total_amount, ed.contact_name,
                ed.contact_phone, ed.event_date, ed.event_address,
                ed.delivery_method, p.payment_method, p.payment_status
            LIMIT 1
        """, (event_id,))
        row = cursor.fetchone()

        if not row:
            return "Event order not found", 404

        event = {
            "customer_name": row["contact_name"],
            "phone_number": row["contact_phone"],
            "event_date": row["event_date"],
            "event_address": row["event_address"],
            "delivery_method": row["delivery_method"],
            "small": int(row["small"] or 0),
            "large": int(row["large"] or 0),
            "total_portions": int(row["small"] or 0) + int(row["large"] or 0),
            "total": float(row["total_amount"] or 0),
            "payment_method": row["payment_method"] or "-",
            "payment_status": row["payment_status"] or "Pending",
            "order_status": row["order_status"] or "Pending",
            "order_type": row["order_type"],
            "order_date": row["order_date"]
        }

        return render_template(
            "admin_event_order_details.html",
            event_id=row["order_number"],
            event=event
        )

    except Exception as e:
        if conn:
            conn.rollback()
        return f"Event Order Details Error: {str(e)}", 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# =====================================
# 12. PAYMENT MANAGEMENT
# =====================================

def get_payment_records():
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT
                p.payment_id,
                p.order_id AS db_order_id,
                o.order_number,
                o.order_type,
                p.amount,
                p.payment_method,
                p.payment_status,
                ed.contact_name
            FROM payments p
            JOIN orders o ON o.order_id = p.order_id
            LEFT JOIN event_details ed ON ed.order_id = o.order_id
            ORDER BY p.payment_id DESC
        """)

        payment_records = {}
        for row in cursor.fetchall():
            payment_key = f"PAY-{row['order_number']}"
            payment_records[payment_key] = {
                "payment_id": payment_key,
                "db_payment_id": row["payment_id"],
                "db_order_id": row["db_order_id"],
                "order_id": row["order_number"],
                "order_type": row["order_type"],
                "customer_name": row["contact_name"] or "QR Customer",
                "amount": float(row["amount"] or 0),
                "payment_method": row["payment_method"] or "-",
                "payment_status": row["payment_status"] or "Pending"
            }

        return payment_records

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route("/admin-payments")
def admin_payments():

    payment_records = get_payment_records()

    status_filter = request.args.get("status", "All")
    search_query = request.args.get("search", "").strip().lower()

    filtered_records = {}

    for payment_id, payment in payment_records.items():

        # Filter by payment status
        if status_filter != "All":
            if payment["payment_status"] != status_filter:
                continue

        # Search by payment ID or order ID
        if search_query:
            if (
                search_query not in payment_id.lower()
                and search_query not in payment["order_id"].lower()
                and search_query not in payment["customer_name"].lower()
            ):
                continue

        filtered_records[payment_id] = payment

    return render_template(
        "admin_payment_management.html",
        payment_records=filtered_records,
        status_filter=status_filter,
        search_query=search_query
    )


@app.route(
    "/admin-payment-details/<payment_id>",
    methods=["GET", "POST"]
)
def admin_payment_details(payment_id):

    payment_records = get_payment_records()

    payment = payment_records.get(payment_id)

    if payment is None:
        return "Payment record not found", 404

    if request.method == "POST":

        new_status = request.form.get("payment_status")

        if new_status not in ["Pending", "Paid"]:
            return "Invalid payment status", 400

        conn = None
        cursor = None

        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE payments
                SET payment_status = %s
                WHERE payment_id = %s
            """, (new_status, payment["db_payment_id"]))
            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            return f"Update Payment Error: {str(e)}", 500
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

        return redirect(
            url_for(
                "admin_payment_details",
                payment_id=payment_id
            )
        )

    return render_template(
        "admin_payment_details.html",
        payment=payment
    )



# =====================================
# 13. SALES REPORT
# =====================================

def get_all_sales_orders():
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT
                o.order_id,
                o.order_number AS order_id_display,
                o.order_type,
                DATE_FORMAT(o.order_date, '%Y-%m-%d') AS order_date,
                o.total_amount AS total,
                p.payment_method,
                p.payment_status,
                COALESCE(SUM(CASE WHEN LOWER(pr.size) = 'small'
                    THEN oi.quantity ELSE 0 END), 0) AS small,
                COALESCE(SUM(CASE WHEN LOWER(pr.size) = 'large'
                    THEN oi.quantity ELSE 0 END), 0) AS large,
                CASE WHEN EXISTS (
                    SELECT 1
                    FROM order_items oi2
                    JOIN order_item_toppings oit2
                        ON oit2.order_item_id = oi2.order_item_id
                    JOIN toppings t2 ON t2.topping_id = oit2.topping_id
                    WHERE oi2.order_id = o.order_id
                      AND t2.topping_name = 'Fried Shallots'
                ) THEN 'Yes' ELSE 'No' END AS fried_shallots,
                CASE WHEN EXISTS (
                    SELECT 1
                    FROM order_items oi3
                    JOIN order_item_toppings oit3
                        ON oit3.order_item_id = oi3.order_item_id
                    JOIN toppings t3 ON t3.topping_id = oit3.topping_id
                    WHERE oi3.order_id = o.order_id
                      AND t3.topping_name = 'Black Sesame'
                ) THEN 'Yes' ELSE 'No' END AS black_sesame
            FROM orders o
            LEFT JOIN payments p ON p.order_id = o.order_id
            LEFT JOIN order_items oi ON oi.order_id = o.order_id
            LEFT JOIN products pr ON pr.product_id = oi.product_id
            GROUP BY o.order_id, o.order_number, o.order_type,
                o.order_date, o.total_amount,
                p.payment_method, p.payment_status
            ORDER BY o.order_id DESC
        """)

        return [
            {
                "order_id": row["order_id_display"],
                "order_type": row["order_type"] or "Normal Order",
                "order_date": row["order_date"],
                "total": float(row["total"] or 0),
                "small": int(row["small"] or 0),
                "large": int(row["large"] or 0),
                "fried_shallots": row["fried_shallots"],
                "black_sesame": row["black_sesame"],
                "payment_method": row["payment_method"] or "Not Provided",
                "payment_status": row["payment_status"] or "Pending"
            }
            for row in cursor.fetchall()
        ]

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def get_sales_summary(start_date, end_date):

    all_orders = get_all_sales_orders()

    paid_orders = []

    for order in all_orders:

        if order["payment_status"] != "Paid":
            continue

        try:
            order_date = datetime.strptime(
                order["order_date"], "%Y-%m-%d"
            ).date()
        except (ValueError, TypeError):
            continue

        if start_date <= order_date <= end_date:
            paid_orders.append(order)

    total_sales = sum(order["total"] for order in paid_orders)

    return {
        "orders": paid_orders,
        "total_sales": total_sales,
        "total_orders": len(paid_orders),
        "small": sum(order["small"] for order in paid_orders),
        "large": sum(order["large"] for order in paid_orders),
        "cash": sum(
            order["total"] for order in paid_orders
            if order["payment_method"] == "Cash"
        ),
        "tng": sum(
            order["total"] for order in paid_orders
            if order["payment_method"] == "Touch 'n Go eWallet"
        ),
        "fried_shallots": sum(
            1 for order in paid_orders
            if order["fried_shallots"] == "Yes"
        ),
        "black_sesame": sum(
            1 for order in paid_orders
            if order["black_sesame"] == "Yes"
        )
    }


@app.route("/admin-sales")
def admin_sales():

    period = request.args.get("period", "daily").lower()

    if period not in ["daily", "weekly", "monthly"]:
        period = "daily"

    today = datetime.now().date()

    # Current reporting period
    if period == "daily":
        start_date = today
        previous_start = today - timedelta(days=1)
        previous_end = previous_start
        period_label = "Today"
        previous_label = "Yesterday"

    elif period == "weekly":
        start_date = today - timedelta(days=today.weekday())
        previous_start = start_date - timedelta(days=7)
        previous_end = start_date - timedelta(days=1)
        period_label = "This Week"
        previous_label = "Previous Week"

    else:
        start_date = today.replace(day=1)
        previous_end = start_date - timedelta(days=1)
        previous_start = previous_end.replace(day=1)
        period_label = "This Month"
        previous_label = "Previous Month"

    current = get_sales_summary(start_date, today)

    # Daily sales value used by the Period Summary section in admin_sales.html
    daily_summary = get_sales_summary(today, today)
    daily_sales = daily_summary["total_sales"]

    previous = get_sales_summary(previous_start, previous_end)

    # Compare with previous period
    difference = current["total_sales"] - previous["total_sales"]

    if previous["total_sales"] > 0:
        percentage_change = (
            difference / previous["total_sales"]
        ) * 100
    else:
        percentage_change = 100 if current["total_sales"] > 0 else 0

    # Sales trend
    trend_labels = []
    trend_values = []

    if period == "daily":

        # Show the last 7 days
        for i in range(6, -1, -1):

            day = today - timedelta(days=i)
            summary = get_sales_summary(day, day)

            trend_labels.append(day.strftime("%d %b"))
            trend_values.append(round(summary["total_sales"], 2))

    elif period == "weekly":

        # Show the last 7 weeks
        for i in range(6, -1, -1):

            week_start = start_date - timedelta(weeks=i)
            week_end = min(
                week_start + timedelta(days=6),
                today
            )

            summary = get_sales_summary(week_start, week_end)

            trend_labels.append(week_start.strftime("%d %b"))
            trend_values.append(round(summary["total_sales"], 2))

    else:

        # Show the last 6 months
        for i in range(5, -1, -1):

            month_start = (
                today.replace(day=1) - timedelta(days=1)
            )

            for _ in range(i):
                month_start = month_start.replace(day=1) - timedelta(days=1)

            month_start = month_start.replace(day=1)

            if month_start.month == 12:
                next_month = month_start.replace(
                    year=month_start.year + 1,
                    month=1
                )
            else:
                next_month = month_start.replace(
                    month=month_start.month + 1
                )

            month_end = min(next_month - timedelta(days=1), today)

            summary = get_sales_summary(month_start, month_end)

            trend_labels.append(month_start.strftime("%b"))
            trend_values.append(round(summary["total_sales"], 2))

    # Best sellers
    best_sellers = [
        {
            "name": "Small Muah Chee",
            "quantity": current["small"]
        },
        {
            "name": "Large Muah Chee",
            "quantity": current["large"]
        },
        {
            "name": "Fried Shallots",
            "quantity": current["fried_shallots"]
        },
        {
            "name": "Black Sesame",
            "quantity": current["black_sesame"]
        }
    ]

    best_sellers.sort(
        key=lambda item: item["quantity"],
        reverse=True
    )

    return render_template(
        "admin_sales.html",
        period=period,
        period_label=period_label,
        previous_label=previous_label,
        daily_sales=daily_sales,
        start_date=start_date.strftime("%d %b %Y"),
        end_date=today.strftime("%d %b %Y"),
        total_sales=current["total_sales"],
        total_orders=current["total_orders"],
        small_sold=current["small"],
        large_sold=current["large"],
        total_portions=current["small"] + current["large"],
        cash_sales=current["cash"],
        tng_sales=current["tng"],
        previous_sales=previous["total_sales"],
        difference=difference,
        percentage_change=percentage_change,
        trend_labels=trend_labels,
        trend_values=trend_values,
        best_sellers=best_sellers,
        report_orders=current["orders"]
    )


# =====================================
# 14. DOWNLOAD SALES REPORT PDF
# =====================================

@app.route("/admin-sales-pdf")
def admin_sales_pdf():

    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib import colors

    period = request.args.get("period", "daily").lower()

    if period not in ["daily", "weekly", "monthly"]:
        period = "daily"

    today = datetime.now().date()

    if period == "daily":
        start_date = today
        period_label = "Daily Sales Report"

    elif period == "weekly":
        start_date = today - timedelta(days=today.weekday())
        period_label = "Weekly Sales Report"

    else:
        start_date = today.replace(day=1)
        period_label = "Monthly Sales Report"

    summary = get_sales_summary(start_date, today)

    buffer = BytesIO()

    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    y = height - 55

    pdf.setTitle(period_label)

    pdf.setFillColor(colors.HexColor("#542b19"))
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(50, y, "Penang Muah Chee")

    y -= 30

    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(50, y, period_label)

    y -= 25

    pdf.setFont("Helvetica", 10)
    pdf.drawString(
        50, y,
        f"Period: {start_date.strftime('%d %b %Y')} - "
        f"{today.strftime('%d %b %Y')}"
    )

    y -= 40

    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(50, y, "Sales Summary")

    y -= 25

    pdf.setFont("Helvetica", 11)

    summary_lines = [
        f"Total Sales: RM{summary['total_sales']:.2f}",
        f"Paid Orders: {summary['total_orders']}",
        f"Small Muah Chee Sold: {summary['small']}",
        f"Large Muah Chee Sold: {summary['large']}",
        f"Total Portions: {summary['small'] + summary['large']}",
        f"Cash Sales: RM{summary['cash']:.2f}",
        f"Touch 'n Go eWallet Sales: RM{summary['tng']:.2f}"
    ]

    for line in summary_lines:
        pdf.drawString(50, y, line)
        y -= 20

    y -= 15

    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(50, y, "Paid Orders")

    y -= 25

    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(50, y, "Order ID")
    pdf.drawString(150, y, "Order Type")
    pdf.drawString(280, y, "Payment")
    pdf.drawRightString(550, y, "Amount")

    y -= 15

    pdf.setFont("Helvetica", 9)

    for order in summary["orders"]:

        if y < 70:
            pdf.showPage()
            y = height - 60
            pdf.setFont("Helvetica", 9)

        pdf.drawString(50, y, order["order_id"])
        pdf.drawString(150, y, order["order_type"][:18])
        pdf.drawString(280, y, order["payment_method"][:25])
        pdf.drawRightString(550, y, f"RM{order['total']:.2f}")

        y -= 18

    pdf.save()

    buffer.seek(0)

    from flask import send_file

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"Muah_Chee_{period}_Sales_Report.pdf",
        mimetype="application/pdf"
    )


# =====================================
# 14. PURCHASE MANAGEMENT
# =====================================

def purchase_key(db_purchase_id):
    return f"PO-{int(db_purchase_id):04d}"


def parse_purchase_key(purchase_id):
    try:
        return int(str(purchase_id).rsplit("-", 1)[-1])
    except (TypeError, ValueError):
        return None


def get_purchase_records():
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT
                p.purchase_id,
                s.supplier_name,
                s.contact_number,
                p.purchase_date,
                pi.material_name,
                pi.quantity,
                pi.unit,
                pi.unit_cost,
                p.total_cost,
                p.payment_method,
                p.notes
            FROM purchases p
            JOIN suppliers s ON s.supplier_id = p.supplier_id
            LEFT JOIN purchase_items pi ON pi.purchase_id = p.purchase_id
            ORDER BY p.purchase_id DESC, pi.purchase_item_id
        """)

        records = {}
        for row in cursor.fetchall():
            key = purchase_key(row["purchase_id"])
            records[key] = {
                "supplier_name": row["supplier_name"],
                "contact_number": row["contact_number"] or "",
                "purchase_date": row["purchase_date"],
                "material_name": row["material_name"] or "",
                "quantity": float(row["quantity"] or 0),
                "unit": row["unit"] or "",
                "unit_cost": float(row["unit_cost"] or 0),
                "total_cost": float(row["total_cost"] or 0),
                "payment_method": row["payment_method"] or "Not recorded",
                "notes": row["notes"] or ""
            }
        return records

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route("/admin-purchases")
def admin_purchases():

    search = request.args.get("search", "").strip().lower()
    purchases = get_purchase_records()

    filtered_purchases = {}

    for purchase_id, purchase in purchases.items():

        if search:
            if (
                search not in purchase_id.lower()
                and search not in purchase["supplier_name"].lower()
                and search not in purchase["material_name"].lower()
            ):
                continue

        filtered_purchases[purchase_id] = purchase

    total_cost = sum(
        purchase["total_cost"]
        for purchase in filtered_purchases.values()
    )

    return render_template(
        "admin_purchases.html",
        purchases=filtered_purchases,
        search=search,
        total_cost=total_cost
    )


@app.route("/admin-add-purchase", methods=["GET", "POST"])
def admin_add_purchase():

    if request.method == "POST":

        supplier_name = request.form.get("supplier_name", "").strip()
        contact_number = request.form.get("contact_number", "").strip()
        purchase_date = request.form.get("purchase_date")
        material_name = request.form.get("material_name", "").strip()
        unit = request.form.get("unit", "").strip()
        payment_method = request.form.get("payment_method")
        notes = request.form.get("notes", "").strip()

        try:
            quantity = float(request.form.get("quantity", 0))
            unit_cost = float(request.form.get("unit_cost", 0))
        except ValueError:
            return "Invalid quantity or unit cost", 400

        if not supplier_name or not material_name or not purchase_date:
            return "Please complete required fields", 400

        if quantity <= 0 or unit_cost < 0:
            return "Invalid quantity or unit cost", 400

        if payment_method not in [
            "Cash", "Bank Transfer", "Touch 'n Go eWallet"
        ]:
            return "Invalid payment method", 400

        total_cost = quantity * unit_cost
        conn = None
        cursor = None

        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)

            cursor.execute("""
                SELECT supplier_id FROM suppliers
                WHERE supplier_name = %s
                ORDER BY supplier_id LIMIT 1
            """, (supplier_name,))
            supplier = cursor.fetchone()

            if supplier:
                supplier_id = supplier["supplier_id"]
                cursor.execute("""
                    UPDATE suppliers SET contact_number = %s
                    WHERE supplier_id = %s
                """, (contact_number or None, supplier_id))
            else:
                cursor.execute("""
                    INSERT INTO suppliers
                        (supplier_name, contact_number, supplier_address)
                    VALUES (%s, %s, NULL)
                """, (supplier_name, contact_number or None))
                supplier_id = cursor.lastrowid

            cursor.execute("""
                SELECT user_id FROM users
                WHERE LOWER(role) = 'admin'
                ORDER BY user_id LIMIT 1
            """)
            admin = cursor.fetchone()
            if not admin:
                raise ValueError("No admin user exists in the users table.")

            cursor.execute("""
                INSERT INTO purchases
                    (supplier_id, user_id, purchase_date, total_cost,
                     payment_method, notes)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                supplier_id, admin["user_id"], purchase_date,
                total_cost, payment_method, notes or None
            ))
            db_purchase_id = cursor.lastrowid

            cursor.execute("""
                INSERT INTO purchase_items
                    (purchase_id, material_name, quantity, unit,
                     unit_cost, subtotal)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                db_purchase_id, material_name, quantity, unit,
                unit_cost, total_cost
            ))

            conn.commit()
            return redirect(url_for(
                "admin_purchase_details",
                purchase_id=purchase_key(db_purchase_id)
            ))

        except ValueError as e:
            if conn:
                conn.rollback()
            return str(e), 400
        except Exception as e:
            if conn:
                conn.rollback()
            return f"Add Purchase Error: {str(e)}", 500
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    return render_template("admin_add_purchase.html")


@app.route(
    "/admin-purchase-details/<purchase_id>",
    methods=["GET", "POST"]
)
def admin_purchase_details(purchase_id):

    db_purchase_id = parse_purchase_key(purchase_id)
    if db_purchase_id is None:
        return "Purchase not found", 404

    purchase = get_purchase_records().get(purchase_key(db_purchase_id))

    if purchase is None:
        return "Purchase not found", 404

    if request.method == "POST":

        supplier_name = request.form.get("supplier_name", "").strip()
        contact_number = request.form.get("contact_number", "").strip()
        purchase_date = request.form.get("purchase_date")
        material_name = request.form.get("material_name", "").strip()
        unit = request.form.get("unit", "").strip()
        payment_method = request.form.get("payment_method")
        notes = request.form.get("notes", "").strip()

        try:
            quantity = float(request.form.get("quantity", 0))
            unit_cost = float(request.form.get("unit_cost", 0))
        except ValueError:
            return "Invalid quantity or unit cost", 400

        if not supplier_name or not material_name or not purchase_date:
            return "Please complete required fields", 400

        if quantity <= 0 or unit_cost < 0:
            return "Invalid quantity or unit cost", 400

        if payment_method not in [
            "Cash", "Bank Transfer", "Touch 'n Go eWallet"
        ]:
            return "Invalid payment method", 400

        conn = None
        cursor = None

        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                SELECT supplier_id FROM suppliers
                WHERE supplier_name = %s
                ORDER BY supplier_id LIMIT 1
            """, (supplier_name,))
            supplier = cursor.fetchone()

            if supplier:
                supplier_id = supplier["supplier_id"]
                cursor.execute("""
                    UPDATE suppliers SET contact_number = %s
                    WHERE supplier_id = %s
                """, (contact_number or None, supplier_id))
            else:
                cursor.execute("""
                    INSERT INTO suppliers
                        (supplier_name, contact_number, supplier_address)
                    VALUES (%s, %s, NULL)
                """, (supplier_name, contact_number or None))
                supplier_id = cursor.lastrowid

            total_cost = quantity * unit_cost
            cursor.execute("""
                UPDATE purchases
                SET supplier_id = %s, purchase_date = %s, total_cost = %s,
                    payment_method = %s, notes = %s
                WHERE purchase_id = %s
            """, (
                supplier_id, purchase_date, total_cost,
                payment_method, notes or None, db_purchase_id
            ))

            cursor.execute("""
                SELECT purchase_item_id FROM purchase_items
                WHERE purchase_id = %s
                ORDER BY purchase_item_id LIMIT 1
            """, (db_purchase_id,))
            item = cursor.fetchone()
            if item:
                cursor.execute("""
                    UPDATE purchase_items
                    SET material_name = %s, quantity = %s, unit = %s,
                        unit_cost = %s, subtotal = %s
                    WHERE purchase_item_id = %s
                """, (
                    material_name, quantity, unit, unit_cost,
                    total_cost, item["purchase_item_id"]
                ))
            else:
                cursor.execute("""
                    INSERT INTO purchase_items
                        (purchase_id, material_name, quantity, unit,
                         unit_cost, subtotal)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    db_purchase_id, material_name, quantity,
                    unit, unit_cost, total_cost
                ))

            conn.commit()
            return redirect(url_for(
                "admin_purchase_details",
                purchase_id=purchase_key(db_purchase_id)
            ))
        except Exception as e:
            if conn:
                conn.rollback()
            return f"Update Purchase Error: {str(e)}", 500
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    return render_template(
        "admin_purchase_details.html",
        purchase_id=purchase_id,
        purchase=purchase
    )


@app.route(
    "/admin-delete-purchase/<purchase_id>",
    methods=["POST"]
)
def admin_delete_purchase(purchase_id):
    db_purchase_id = parse_purchase_key(purchase_id)
    if db_purchase_id is None:
        return "Purchase not found", 404

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM purchase_items WHERE purchase_id = %s",
            (db_purchase_id,)
        )
        cursor.execute(
            "DELETE FROM purchases WHERE purchase_id = %s",
            (db_purchase_id,)
        )
        conn.commit()
        return redirect(url_for("admin_purchases"))
    except Exception as e:
        if conn:
            conn.rollback()
        return f"Delete Purchase Error: {str(e)}", 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()



# =====================================
# 15. PRODUCT SETTINGS
# =====================================

product_settings = {
    "small_price": 3.50,
    "large_price": 5.00,

    "small_available": True,
    "large_available": True,

    "fried_shallots_available": True,
    "black_sesame_available": True,

    "product_available": True
}



@app.route("/admin-product-settings", methods=["GET", "POST"])
def admin_product_settings():

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # =================================
        # UPDATE PRODUCT SETTINGS
        # =================================

        if request.method == "POST":

            try:
                small_price = float(request.form.get("small_price", 3.50))
                large_price = float(request.form.get("large_price", 5.00))
            except (ValueError, TypeError):
                return "Invalid product price", 400

            if small_price < 0 or large_price < 0:
                return "Price cannot be negative", 400

            small_available = request.form.get("small_available") == "on"
            large_available = request.form.get("large_available") == "on"

            fried_available = (
                request.form.get("fried_shallots_available") == "on"
            )

            sesame_available = (
                request.form.get("black_sesame_available") == "on"
            )

            ordering_available = (
                request.form.get("product_available") == "on"
            )

            # Validate expected database records before changing any values.
            cursor.execute("""
                SELECT
                    (SELECT COUNT(*) FROM products WHERE LOWER(size) = 'small') AS small_count,
                    (SELECT COUNT(*) FROM products WHERE LOWER(size) = 'large') AS large_count,
                    (SELECT COUNT(*) FROM toppings WHERE topping_name = 'Fried Shallots') AS shallots_count,
                    (SELECT COUNT(*) FROM toppings WHERE topping_name = 'Black Sesame') AS sesame_count,
                    (SELECT COUNT(*) FROM system_settings WHERE setting_key = 'product_available') AS setting_count
            """)
            counts = cursor.fetchone()
            if any(counts[key] != 1 for key in counts):
                raise ValueError(
                    "Database must contain exactly one Small and Large product, "
                    "both toppings, and one product_available setting."
                )

            # Update products by their database labels, not assumed IDs.
            cursor.execute("""
                UPDATE products
                SET price = %s, availability = %s
                WHERE LOWER(size) = 'small'
            """, (small_price, small_available))

            cursor.execute("""
                UPDATE products
                SET price = %s, availability = %s
                WHERE LOWER(size) = 'large'
            """, (large_price, large_available))

            cursor.execute("""
                UPDATE toppings
                SET availability = %s
                WHERE topping_name = 'Fried Shallots'
            """, (fried_available,))

            cursor.execute("""
                UPDATE toppings
                SET availability = %s
                WHERE topping_name = 'Black Sesame'
            """, (sesame_available,))

            # Update overall ordering availability
            cursor.execute("""
                UPDATE system_settings
                SET setting_value = %s
                WHERE setting_key = 'product_available'
            """, ("1" if ordering_available else "0",))

            conn.commit()

            # Synchronise the existing Python settings
            product_settings.update({
                "small_price": small_price,
                "large_price": large_price,
                "small_available": small_available,
                "large_available": large_available,
                "fried_shallots_available": fried_available,
                "black_sesame_available": sesame_available,
                "product_available": ordering_available
            })

            return redirect(url_for("admin_product_settings"))

        # =================================
        # READ PRODUCT SETTINGS FROM MYSQL
        # =================================

        cursor.execute("SELECT * FROM products")
        product_rows = cursor.fetchall()

        cursor.execute("SELECT * FROM toppings")
        topping_rows = cursor.fetchall()

        cursor.execute("""
            SELECT setting_value
            FROM system_settings
            WHERE setting_key = 'product_available'
        """)
        setting_row = cursor.fetchone()

        # Default values
        products = {
            "small_price": 3.50,
            "large_price": 5.00,
            "small_available": False,
            "large_available": False,
            "fried_shallots_available": False,
            "black_sesame_available": False,
            "product_available": (
                setting_row["setting_value"] == "1"
                if setting_row else True
            )
        }

        # Read product prices and availability
        for product in product_rows:

            if product["size"].lower() == "small":
                products["small_price"] = float(product["price"])
                products["small_available"] = bool(product["availability"])

            elif product["size"].lower() == "large":
                products["large_price"] = float(product["price"])
                products["large_available"] = bool(product["availability"])

        # Read topping availability
        for topping in topping_rows:

            if topping["topping_name"] == "Fried Shallots":
                products["fried_shallots_available"] = bool(
                    topping["availability"]
                )

            elif topping["topping_name"] == "Black Sesame":
                products["black_sesame_available"] = bool(
                    topping["availability"]
                )

        # Synchronise Python settings
        product_settings.update(products)

        # Display settings in HTML
        return render_template(
            "admin_product_settings.html",
            products=products,
            settings=products
        )

    except Exception as e:

        if conn:
            conn.rollback()

        return f"Product Settings Error: {str(e)}", 500

    finally:

        if cursor:
            cursor.close()

        if conn:
            conn.close()


@app.route("/test-db")
def test_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT DATABASE()")
        result = cursor.fetchone()

        cursor.close()
        conn.close()

        return f"Database connected successfully: {result[0]}"

    except Error as e:
        return f"Database connection failed: {e}"
# =====================================
# 16. RUN APPLICATION
# =====================================

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
