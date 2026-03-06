from flask import (
    Flask, render_template, redirect, url_for,
    request, session, flash, make_response
)
from datetime import date, datetime, timedelta
from functools import wraps
import calendar
import csv
from io import StringIO
from apscheduler.schedulers.background import BackgroundScheduler
import smtplib
import os

from werkzeug.security import generate_password_hash, check_password_hash

from database import get_connection, init_db
from itsdangerous import URLSafeTimedSerializer

# Initialize database tables when app starts
init_db()

# ============================================================
#   APP CONFIG
# ============================================================

app = Flask(__name__)
app.secret_key = "change-me-in-real-project"
serializer = URLSafeTimedSerializer(app.secret_key)
# Email config (set these as environment variables on your PC)
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.environ.get("APP_SMTP_USER")  # your email
SMTP_PASS = os.environ.get("APP_SMTP_PASS")  # app password

INCOME_CATEGORIES = [
    "Salary",
    "Freelance",
    "Scholarship",
    "Gift",
    "Other Income",
]

EXPENSE_CATEGORIES = [
    "Food",
    "Transport",
    "Rent",
    "Utilities",
    "Entertainment",
    "Shopping",
    "Bills",
    "Other Expense",
]


# ============================================================
#   AUTH HELPERS
# ============================================================
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth_login"))
        return view(*args, **kwargs)
    return wrapped


def current_user_id():
    return session.get("user_id")


# ============================================================
#   EMAIL HELPER
# ============================================================
import resend
import os

resend.api_key = os.environ["RESEND_API_KEY"]

def send_email(to_email, subject, body):
    resend.Emails.send({
        "from": "personalfinancetrackerr@gmail.com",
        "to": [to_email],
        "subject": subject,
        "html": f"<p>{body}</p>"
    })

# ============================================================
#   NOTIFICATION HELPERS
# ============================================================
def get_unread_notification_count(user_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND is_read = 0",
        (user_id,)
    )
    row = cur.fetchone()
    conn.close()
    return row["c"] if row else 0


@app.context_processor
def inject_globals():
    """
    Makes unread_count available in all templates (for navbar bell icon).
    """
    user_id = current_user_id()
    unread_count = 0
    if user_id:
        try:
            unread_count = get_unread_notification_count(user_id)
        except Exception:
            unread_count = 0
    return dict(unread_count=unread_count)


# ============================================================
#   RECURRING LOGIC (AUTO-APPLY TRANSACTIONS)
# ============================================================
def apply_due_recurring(user_id: int):
    """
    Update next_date for recurring items that are due.
    DOES NOT create normal transactions anymore.
    """
    today = date.today()
    today_str = today.isoformat()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM recurring_transactions
        WHERE active = 1
          AND user_id = ?
          AND next_date <= ?
    """, (user_id, today_str))

    rows = cur.fetchall()

    for r in rows:
        current = datetime.strptime(r["next_date"], "%Y-%m-%d").date()
        freq = r["frequency"]

        # Move next date based on frequency
        if freq == "Daily":
            new_date = current + timedelta(days=1)
        elif freq == "Weekly":
            new_date = current + timedelta(days=7)
        elif freq == "Monthly":
            year = current.year
            month = current.month + 1
            if month > 12:
                month = 1
                year += 1
            day = current.day
            days_in_month = calendar.monthrange(year, month)[1]
            if day > days_in_month:
                day = days_in_month
            new_date = date(year, month, day)
        elif freq == "Yearly":
            try:
                new_date = date(current.year + 1, current.month, current.day)
            except ValueError:
                new_date = date(current.year + 1, current.month, 28)  # handle Feb 29
        else:
            new_date = current + timedelta(days=30)

        # Update only next_date
        cur.execute("""
            UPDATE recurring_transactions
            SET next_date = ?
            WHERE id = ?
        """, (new_date.isoformat(), r["id"]))

    conn.commit()
    conn.close()


# ============================================================
#   REMINDER CHECK (IN-APP + EMAIL)
# ============================================================
def check_recurring_reminders(for_user_id: int | None = None):
    """
    Prevent duplicate reminders by tracking last_reminded_on.
    """
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    conn = get_connection()
    cur = conn.cursor()

    query = """
        SELECT r.*, u.email
        FROM recurring_transactions r
        JOIN users u ON r.user_id = u.id
        WHERE (r.next_date = ? OR r.next_date = ?)
          AND r.reminder_type IS NOT NULL
          AND r.reminder_type != ''
    """
    params = [today, tomorrow]

    if for_user_id:
        query += " AND r.user_id = ?"
        params.append(for_user_id)

    cur.execute(query, params)
    rows = cur.fetchall()

    for r in rows:

        # --- STOP SPAM ---
        # If already reminded today, SKIP
        if r["last_reminded_on"] == today:
            continue

        msg = (
            f"Reminder: Your {r['description']} ({r['category']}) "
            f"of ${r['amount']} is due on {r['next_date']}."
        )

        # In-app
        if r["reminder_type"] in ("In-App", "Both"):
            cur.execute("""
                INSERT INTO notifications (user_id, message, created_at, is_read)
                VALUES (?, ?, ?, 0)
            """, (r["user_id"], msg, today))

        # Email
        if r["reminder_type"] in ("Email", "Both"):
            send_email(r["email"], "Bill Reminder", msg)

        # Mark it as reminded today
        cur.execute("""
            UPDATE recurring_transactions
            SET last_reminded_on = ?
            WHERE id = ?
        """, (today, r["id"]))

    conn.commit()
    conn.close()


# ============================================================
#   ROOT
# ============================================================
@app.route("/")
def root():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("auth_login"))


# ============================================================
#   DASHBOARD
# ============================================================
@app.route("/dashboard")
@login_required
def dashboard():
    user_id = current_user_id()

    # Apply recurring + check reminders "live"
    apply_due_recurring(user_id)
    check_recurring_reminders(for_user_id=user_id)

    name_full = session.get("user_name", "User")
    first_name = name_full.split()[0]

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT COALESCE(SUM(amount),0) AS inc "
        "FROM transactions WHERE user_id = ? AND type='Income'",
        (user_id,)
    )
    total_income = cur.fetchone()["inc"]

    cur.execute(
        "SELECT COALESCE(SUM(amount),0) AS exp "
        "FROM transactions WHERE user_id = ? AND type='Expense'",
        (user_id,)
    )
    total_expenses = cur.fetchone()["exp"]

    balance = total_income - total_expenses

    cur.execute("""
        SELECT *
        FROM transactions
        WHERE user_id = ?
        ORDER BY date DESC, id DESC
        LIMIT 5
    """, (user_id,))
    recent = cur.fetchall()

    conn.close()

    return render_template(
        "dashboard.html",
        first_name=first_name,
        total_income=total_income,
        total_expenses=total_expenses,
        balance=balance,
        recent_transactions=recent
    )


# ============================================================
#   TRANSACTIONS LIST
# ============================================================
@app.route("/transactions")
@login_required
def transactions():
    user_id = current_user_id()

    apply_due_recurring(user_id)
    check_recurring_reminders(for_user_id=user_id)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM transactions WHERE user_id=? ORDER BY date DESC",
        (user_id,)
    )
    rows = cur.fetchall()
    conn.close()

    return render_template("transactions.html", transactions=rows)


# ============================================================
#   ADD TRANSACTION
# ============================================================
@app.route("/transactions/add", methods=["GET", "POST"])
@login_required
def add_transaction():
    user_id = current_user_id()

    if request.method == "POST":
        date_val = request.form.get("date")
        desc = request.form.get("description") or ""
        category = request.form.get("category")
        ttype = request.form.get("type")
        amount = request.form.get("amount")

        if not (date_val and category and ttype and amount):
            flash("Please fill all fields.", "danger")
            return redirect(url_for("add_transaction"))

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO transactions (user_id, date, description, category, type, amount)
            VALUES (?,?,?,?,?,?)
        """, (user_id, date_val, desc, category, ttype, float(amount)))
        conn.commit()
        conn.close()

        return redirect(url_for("transactions"))

    return render_template(
        "transactions_add.html",
        expense_categories=EXPENSE_CATEGORIES,
        income_categories=INCOME_CATEGORIES
    )


# ============================================================
#   DELETE TRANSACTION
# ============================================================
@app.route("/transactions/delete/<int:tid>", methods=["POST"])
@login_required
def delete_transaction(tid):
    user_id = current_user_id()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM transactions WHERE id = ? AND user_id = ?",
        (tid, user_id)
    )
    conn.commit()
    conn.close()
    return redirect(url_for("transactions"))

# ============================================================
#   EDIT / UPDATE TRANSACTION
# ============================================================
@app.route("/transactions/edit/<int:tid>", methods=["GET", "POST"])
@login_required
def edit_transaction(tid):
    user_id = current_user_id()

    conn = get_connection()
    cur = conn.cursor()

    # Fetch existing transaction
    cur.execute(
        "SELECT * FROM transactions WHERE id = ? AND user_id = ?",
        (tid, user_id)
    )
    t = cur.fetchone()

    if not t:
        conn.close()
        flash("Transaction not found.", "danger")
        return redirect(url_for("transactions"))

    if request.method == "POST":
        date_val = request.form.get("date")
        desc = request.form.get("description") or ""
        category = request.form.get("category")
        ttype = request.form.get("type")
        amount = request.form.get("amount")

        if not (date_val and category and ttype and amount):
            flash("Please fill all fields.", "danger")
            return redirect(url_for("edit_transaction", tid=tid))

        cur.execute("""
            UPDATE transactions
            SET date = ?, description = ?, category = ?, type = ?, amount = ?
            WHERE id = ? AND user_id = ?
        """, (date_val, desc, category, ttype, float(amount), tid, user_id))

        conn.commit()
        conn.close()

        flash("Transaction updated successfully.", "success")
        return redirect(url_for("transactions"))

    conn.close()

    return render_template(
        "transactions_edit.html",
        t=t,
        expense_categories=EXPENSE_CATEGORIES,
        income_categories=INCOME_CATEGORIES
    )

# ============================================================
#   REPORTS
# ============================================================
@app.route("/reports")
@login_required
def reports():
    user_id = current_user_id()

    apply_due_recurring(user_id)
    check_recurring_reminders(for_user_id=user_id)

    from_date = request.args.get("fromDate")
    to_date = request.args.get("toDate")

    conn = get_connection()
    cur = conn.cursor()

    conditions = ["user_id = ?"]
    params = [user_id]

    if from_date:
        conditions.append("date >= ?")
        params.append(from_date)

    if to_date:
        conditions.append("date <= ?")
        params.append(to_date)

    where = " WHERE " + " AND ".join(conditions)

    cur.execute(
        "SELECT COALESCE(SUM(amount),0) AS t "
        "FROM transactions" + where + " AND type='Income'",
        params
    )
    total_income = cur.fetchone()["t"]

    cur.execute(
        "SELECT COALESCE(SUM(amount),0) AS t "
        "FROM transactions" + where + " AND type='Expense'",
        params
    )
    total_expenses = cur.fetchone()["t"]

    balance = total_income - total_expenses

    cur.execute("""
        SELECT category, SUM(amount) AS total
        FROM transactions
        """ + where + """ AND type='Expense'
        GROUP BY category
        ORDER BY total DESC
    """, params)
    expense_breakdown = cur.fetchall()

    cur.execute(
        "SELECT category, amount FROM budgets WHERE user_id = ?",
        (user_id,)
    )
    budgets_raw = cur.fetchall()
    budgets_map = {b["category"]: b["amount"] for b in budgets_raw}

    cur.execute("""
        SELECT strftime('%Y-%m', date) AS ym,
               SUM(CASE WHEN type='Income' THEN amount ELSE 0 END) AS inc,
               SUM(CASE WHEN type='Expense' THEN amount ELSE 0 END) AS exp
        FROM transactions
        """ + where + """
        GROUP BY ym
        ORDER BY ym
    """, params)
    rows = cur.fetchall()
    month_labels = [r["ym"] for r in rows]
    month_income = [r["inc"] for r in rows]
    month_expense = [r["exp"] for r in rows]

    conn.close()

    return render_template(
        "reports.html",
        total_income=total_income,
        total_expenses=total_expenses,
        balance=balance,
        expense_breakdown=expense_breakdown,
        month_labels=month_labels,
        month_income=month_income,
        month_expense=month_expense,
        from_date=from_date,
        to_date=to_date,
        budgets_map=budgets_map,
    )


# ============================================================
#   EXPORT CSV
# ============================================================
@app.route("/reports/export_csv")
@login_required
def export_csv():
    user_id = current_user_id()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT date,description,category,type,amount
        FROM transactions
        WHERE user_id=?
        ORDER BY date DESC
    """, (user_id,))
    rows = cur.fetchall()
    conn.close()

    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["Date", "Description", "Category", "Type", "Amount"])

    for r in rows:
        writer.writerow([r["date"], r["description"], r["category"], r["type"], r["amount"]])

    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=report.csv"
    output.headers["Content-type"] = "text/csv"
    return output


# ============================================================
#   EXPORT PDF
# ============================================================
@app.route("/reports/export_pdf")
@login_required
def export_pdf():
    from fpdf import FPDF

    user_id = current_user_id()

    conn = get_connection()
    cur = conn.cursor()

    # User info
    cur.execute("SELECT name FROM users WHERE id=?", (user_id,))
    user_name = cur.fetchone()["name"]

    # Summary
    cur.execute("""
        SELECT
            SUM(CASE WHEN type='Income' THEN amount ELSE 0 END) AS income,
            SUM(CASE WHEN type='Expense' THEN amount ELSE 0 END) AS expense
        FROM transactions
        WHERE user_id=?
    """, (user_id,))
    s = cur.fetchone()
    income = s["income"] or 0
    expense = s["expense"] or 0
    balance = income - expense

    # Expense by category
    cur.execute("""
        SELECT category, SUM(amount) AS total
        FROM transactions
        WHERE user_id=? AND type='Expense'
        GROUP BY category
    """, (user_id,))
    expense_by_category = cur.fetchall()

    # Monthly trends
    cur.execute("""
        SELECT
            strftime('%Y-%m', date) AS month,
            SUM(CASE WHEN type='Income' THEN amount ELSE 0 END) AS income,
            SUM(CASE WHEN type='Expense' THEN amount ELSE 0 END) AS expense
        FROM transactions
        WHERE user_id=?
        GROUP BY month
        ORDER BY month
    """, (user_id,))
    monthly = cur.fetchall()

    # Transactions
    cur.execute("""
        SELECT date, description, category, type, amount
        FROM transactions
        WHERE user_id=?
        ORDER BY date DESC
    """, (user_id,))
    transactions = cur.fetchall()

    conn.close()

    # ---------------- PDF ----------------
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Logo (top-right)
    try:
        pdf.image("static/img/logo.png", x=160, y=10, w=35)
    except:
        pass  # if logo missing, PDF still works

    # Title
    pdf.set_font("Arial", "B", 16)
    pdf.set_text_color(30, 64, 124)  # navy blue
    pdf.cell(0, 10, "Personal Finance Report", ln=1)

    pdf.ln(2)
    pdf.set_font("Arial", size=11)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 8, f"Generated for: {user_name}", ln=1)
    pdf.cell(0, 8, f"Generated on: {date.today().strftime('%d %B %Y')}", ln=1)

    # ---------------- Summary ----------------
    pdf.ln(6)
    pdf.set_fill_color(235, 240, 250)
    pdf.set_font("Arial", "B", 13)
    pdf.cell(0, 8, "Summary Overview", ln=1, fill=True)

    pdf.set_font("Arial", size=11)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 8, f"Total Income: ${income:.2f}", ln=1)
    pdf.cell(0, 8, f"Total Expenses: ${expense:.2f}", ln=1)

    # Balance color
    if balance >= 0:
        pdf.set_text_color(0, 150, 0)   # green
    else:
        pdf.set_text_color(200, 0, 0)   # red

    pdf.cell(0, 8, f"Balance: ${balance:.2f}", ln=1)
    pdf.set_text_color(0, 0, 0)

    # ---------------- Expenses by Category ----------------
    pdf.ln(6)
    pdf.set_fill_color(235, 240, 250)
    pdf.set_font("Arial", "B", 13)
    pdf.cell(0, 8, "Expenses by Category", ln=1, fill=True)

    pdf.set_font("Arial", "B", 11)
    pdf.set_fill_color(210, 220, 235)
    pdf.cell(80, 8, "Category", 1, fill=True)
    pdf.cell(40, 8, "Total ($)", 1, fill=True)
    pdf.ln()

    pdf.set_font("Arial", size=11)
    for e in expense_by_category:
        pdf.cell(80, 8, e["category"], 1)
        pdf.cell(40, 8, f"{e['total']:.2f}", 1)
        pdf.ln()

    # ---------------- Monthly Trends ----------------
    pdf.ln(6)
    pdf.set_fill_color(235, 240, 250)
    pdf.set_font("Arial", "B", 13)
    pdf.cell(0, 8, "Monthly Trends", ln=1, fill=True)

    pdf.set_font("Arial", "B", 11)
    pdf.set_fill_color(210, 220, 235)
    pdf.cell(50, 8, "Month", 1, fill=True)
    pdf.cell(40, 8, "Income", 1, fill=True)
    pdf.cell(40, 8, "Expenses", 1, fill=True)
    pdf.ln()

    pdf.set_font("Arial", size=11)
    for m in monthly:
        pdf.cell(50, 8, m["month"], 1)
        pdf.cell(40, 8, f"{m['income']:.2f}", 1)
        pdf.cell(40, 8, f"{m['expense']:.2f}", 1)
        pdf.ln()

    # ---------------- All Transactions ----------------
    pdf.ln(6)
    pdf.set_fill_color(235, 240, 250)
    pdf.set_font("Arial", "B", 13)
    pdf.cell(0, 8, "All Transactions", ln=1, fill=True)

    pdf.set_font("Arial", "B", 10)
    pdf.set_fill_color(210, 220, 235)
    pdf.cell(30, 8, "Date", 1, fill=True)
    pdf.cell(50, 8, "Description", 1, fill=True)
    pdf.cell(30, 8, "Category", 1, fill=True)
    pdf.cell(20, 8, "Type", 1, fill=True)
    pdf.cell(30, 8, "Amount", 1, fill=True)
    pdf.ln()

    pdf.set_font("Arial", size=10)
    for t in transactions:
        pdf.cell(30, 8, t["date"], 1)
        pdf.cell(50, 8, t["description"] or "-", 1)
        pdf.cell(30, 8, t["category"], 1)
        pdf.cell(20, 8, t["type"], 1)
        pdf.cell(30, 8, f"${t['amount']:.2f}", 1)
        pdf.ln()

    response = make_response(pdf.output(dest="S").encode("latin-1"))
    response.headers["Content-Disposition"] = "attachment; filename=finance_report.pdf"
    response.headers["Content-Type"] = "application/pdf"
    return response


# ============================================================
#   BUDGETS
# ============================================================
@app.route("/budgets", methods=["GET", "POST"])
@login_required
def manage_budgets():
    user_id = current_user_id()

    apply_due_recurring(user_id)
    check_recurring_reminders(for_user_id=user_id)

    conn = get_connection()
    cur = conn.cursor()

    if request.method == "POST":
        category = request.form.get("category")
        amount_raw = request.form.get("amount")

        if category and amount_raw:
            try:
                amt = float(amount_raw)
                cur.execute("""
                    INSERT INTO budgets (user_id,category,amount)
                    VALUES (?,?,?)
                    ON CONFLICT(user_id,category)
                    DO UPDATE SET amount=excluded.amount
                """, (user_id, category, amt))
                conn.commit()
            except ValueError:
                flash("Invalid amount.", "danger")

        conn.close()
        return redirect(url_for("manage_budgets"))

    cur.execute(
        "SELECT * FROM budgets WHERE user_id=? ORDER BY category",
        (user_id,)
    )
    rows = cur.fetchall()
    conn.close()

    return render_template(
        "budgets.html",
        budgets=rows,
        expense_categories=EXPENSE_CATEGORIES
    )


@app.route("/budgets/delete/<int:bid>", methods=["POST"])
@login_required
def delete_budget(bid):
    user_id = current_user_id()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM budgets WHERE id=? AND user_id=?",
        (bid, user_id)
    )
    conn.commit()
    conn.close()
    return redirect(url_for("manage_budgets"))


# ============================================================
#   RECURRING PAGE
# ============================================================
@app.route("/recurring", methods=["GET", "POST"])
@login_required
def recurring():
    user_id = current_user_id()

    conn = get_connection()
    cur = conn.cursor()

    if request.method == "POST":
        r_type = request.form.get("type")
        category = request.form.get("category")
        amount_raw = request.form.get("amount")
        frequency = request.form.get("frequency")
        next_date = request.form.get("next_date")
        reminder_type = request.form.get("reminder")
        description = request.form.get("description") or ""

        if r_type and category and amount_raw and frequency and next_date:
            try:
                amount = float(amount_raw)
                cur.execute("""
                    INSERT INTO recurring_transactions
                    (user_id,description,category,type,amount,frequency,next_date,reminder_type,active)
                    VALUES (?,?,?,?,?,?,?,?,1)
                """, (
                    user_id, description, category, r_type,
                    amount, frequency, next_date, reminder_type
                ))
                conn.commit()
            except ValueError:
                flash("Amount must be a number.", "danger")

        conn.close()
        return redirect(url_for("recurring"))

    conn.close()

    # On GET: still apply recurring & reminders
    apply_due_recurring(user_id)
    check_recurring_reminders(for_user_id=user_id)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM recurring_transactions WHERE user_id=? ORDER BY next_date",
        (user_id,)
    )
    items = cur.fetchall()
    conn.close()

    return render_template(
        "recurring.html",
        recurring_items=items,
        income_categories=INCOME_CATEGORIES,
        expense_categories=EXPENSE_CATEGORIES
    )


@app.route("/recurring/delete/<int:rid>", methods=["POST"])
@login_required
def delete_recurring(rid):
    user_id = current_user_id()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM recurring_transactions WHERE id=? AND user_id=?",
        (rid, user_id)
    )
    conn.commit()
    conn.close()
    return redirect(url_for("recurring"))


# ============================================================
#   AUTH
# ============================================================
import random

@app.route("/auth/login", methods=["GET", "POST"])
def auth_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE email=?", (email,))
        user = cur.fetchone()
        conn.close()

        if not user:
            flash("User not found.", "danger")
            return redirect(url_for("auth_login"))

        if not check_password_hash(user["password_hash"], password):
            flash("Incorrect password.", "danger")
            return redirect(url_for("auth_login"))

        # ✅ Password correct → Generate OTP
        otp = str(random.randint(100000, 999999))

        session["otp"] = otp
        session["otp_user_id"] = user["id"]
        session["otp_expiry"] = (datetime.now() + timedelta(minutes=5)).timestamp()

        # Send OTP Email
        send_email(
            user["email"],
            "Your Login OTP - Personal Finance Tracker",
            f"Your OTP code is: {otp}\n\nThis code expires in 5 minutes."
        )

        return redirect(url_for("verify_otp"))

    return render_template("auth_login.html")

@app.route("/auth/verify-otp", methods=["GET", "POST"])
def verify_otp():
    if "otp_user_id" not in session:
        return redirect(url_for("auth_login"))

    # 🔁 Handle Resend OTP
    if request.method == "POST" and "resend" in request.form:
        user_id = session.get("otp_user_id")

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT email FROM users WHERE id=?", (user_id,))
        user = cur.fetchone()
        conn.close()

        # Generate new OTP
        otp = str(random.randint(100000, 999999))
        session["otp"] = otp
        session["otp_expiry"] = (datetime.now() + timedelta(minutes=5)).timestamp()

        send_email(
            user["email"],
            "Your New OTP - Personal Finance Tracker",
            f"Your new OTP is: {otp}\n\nThis code expires in 5 minutes."
        )

        flash("New OTP sent to your email.", "info")
        return redirect(url_for("verify_otp"))

    # ✅ Handle OTP verification
    if request.method == "POST":
        entered_otp = request.form.get("otp")

        if datetime.now().timestamp() > session.get("otp_expiry", 0):
            session.clear()
            flash("OTP expired. Please login again.", "danger")
            return redirect(url_for("auth_login"))

        if entered_otp == session.get("otp"):
            user_id = session.get("otp_user_id")

            session.pop("otp", None)
            session.pop("otp_user_id", None)
            session.pop("otp_expiry", None)

            session["user_id"] = user_id

            conn = get_connection()
            cur = conn.cursor()
            cur.execute("SELECT name FROM users WHERE id=?", (user_id,))
            user = cur.fetchone()
            conn.close()

            session["user_name"] = user["name"]

            flash("Login successful!", "success")
            return redirect(url_for("dashboard"))

        else:
            flash("Invalid OTP.", "danger")

    return render_template("auth_verify_otp.html")

@app.route("/auth/register", methods=["GET", "POST"])
def auth_register():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email").strip().lower()
        password = request.form.get("password")

        if not (name and email and password):
            flash("All fields required.", "danger")
            return redirect(url_for("auth_register"))

        pw_hash = generate_password_hash(password)

        conn = get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO users (name,email,password_hash) VALUES (?,?,?)",
                (name, email, pw_hash)
            )
            conn.commit()
        except Exception:
            conn.close()
            flash("Email already registered.", "danger")
            return redirect(url_for("auth_register"))

        conn.close()
        flash("Account created! Please log in.", "success")
        return redirect(url_for("auth_login"))

    return render_template("auth_register.html")


@app.route("/auth/forgot", methods=["GET", "POST"])
def auth_forgot():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE email = ?", (email,))
        user = cur.fetchone()
        conn.close()

        if not user:
            flash("No account found with that email.", "danger")
            return redirect(url_for("auth_forgot"))

        token = serializer.dumps(email, salt="password-reset-salt")
        reset_url = url_for("auth_reset_password", token=token, _external=True)

        send_email(
            email,
            "Password Reset - Personal Finance Tracker",
            f"Click this link to reset your password:\n\n{reset_url}\n\nLink expires in 1 hour."
        )

        flash("Password reset link sent to your email.", "success")
        return redirect(url_for("auth_login"))

    return render_template("auth_forgot.html")

@app.route("/auth/reset/<token>", methods=["GET", "POST"])
def auth_reset_password(token):
    try:
        email = serializer.loads(
            token,
            salt="password-reset-salt",
            max_age=3600
        )
    except Exception:
        flash("Reset link expired or invalid.", "danger")
        return redirect(url_for("auth_login"))

    if request.method == "POST":
        new_pw = request.form.get("new_password")
        confirm_pw = request.form.get("confirm_password")

        if new_pw != confirm_pw:
            flash("Passwords do not match.", "danger")
            return redirect(request.url)

        new_hash = generate_password_hash(new_pw)

        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET password_hash = ? WHERE email = ?",
            (new_hash, email)
        )
        conn.commit()
        conn.close()

        flash("Password reset successful. Please login.", "success")
        return redirect(url_for("auth_login"))

    return render_template("auth_reset_password.html")

@app.route("/auth/logout")
def auth_logout():
    session.clear()
    return redirect(url_for("auth_login"))


# ============================================================
#   PROFILE (OPTION B: 3 SEPARATE PAGES)
# ============================================================
@app.route("/profile")
@login_required
def profile():
    user_id = current_user_id()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = cur.fetchone()
    conn.close()

    return render_template("profile.html", user=user)


@app.route("/profile/edit", methods=["GET", "POST"])
@login_required
def profile_edit():
    user_id = current_user_id()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = cur.fetchone()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()

        if not (name and email):
            flash("Name and email are required.", "danger")
            conn.close()
            return redirect(url_for("profile_edit"))

        try:
            cur.execute("""
                UPDATE users
                SET name = ?, email = ?
                WHERE id = ?
            """, (name, email, user_id))
            conn.commit()
            session["user_name"] = name
            flash("Profile updated successfully!", "success")
        except Exception:
            flash("Email already in use.", "danger")
        finally:
            conn.close()

        return redirect(url_for("profile"))

    conn.close()
    return render_template("profile_edit.html", user=user)


@app.route("/profile/password", methods=["GET", "POST"])
@login_required
def profile_change_password():
    user_id = current_user_id()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = cur.fetchone()

    if request.method == "POST":
        current_pw = request.form.get("current_password")
        new_pw = request.form.get("new_password")
        confirm_pw = request.form.get("confirm_password")

        if not check_password_hash(user["password_hash"], current_pw):
            flash("Current password is incorrect.", "danger")
            conn.close()
            return redirect(url_for("profile_change_password"))

        if new_pw != confirm_pw:
            flash("New passwords do not match.", "danger")
            conn.close()
            return redirect(url_for("profile_change_password"))

        new_hash = generate_password_hash(new_pw)
        cur.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (new_hash, user_id)
        )
        conn.commit()
        conn.close()
        flash("Password changed successfully!", "success")
        return redirect(url_for("profile"))

    conn.close()
    return render_template("profile_change_password.html", user=user)


# ============================================================
#   NOTIFICATIONS PAGE
# ============================================================
@app.route("/notifications")
@login_required
def notifications():
    user_id = current_user_id()

    # Also check reminders when visiting notifications page
    check_recurring_reminders(for_user_id=user_id)

    conn = get_connection()
    cur = conn.cursor()

    # Mark all as read when viewing the page
    cur.execute(
        "UPDATE notifications SET is_read = 1 WHERE user_id = ?",
        (user_id,)
    )
    conn.commit()

    # Fetch all notifications
    cur.execute("""
        SELECT *
        FROM notifications
        WHERE user_id = ?
        ORDER BY created_at DESC
    """, (user_id,))
    notes = cur.fetchall()

    conn.close()
    return render_template("notifications.html", notes=notes)


@app.route("/notifications/read/<int:nid>", methods=["POST"])
@login_required
def mark_notifications_read(nid):
    user_id = current_user_id()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE notifications
        SET is_read = 1
        WHERE id = ? AND user_id = ?
    """, (nid, user_id))

    conn.commit()
    conn.close()

    return redirect(url_for("notifications"))


@app.route("/notifications/clear", methods=["POST"])
@login_required
def clear_notifications():
    """
    Delete all notifications for the logged-in user.
    """
    user_id = current_user_id()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM notifications WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

    # No flash message → avoids showing on other pages
    return redirect(url_for("notifications"))


@app.route("/notifications/mark_all", methods=["POST"])
@login_required
def notifications_mark_all():
    user_id = current_user_id()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

    # No flash → avoids message appearing on profile
    return redirect(url_for("notifications"))


# ============================================================
#   ERROR HANDLER
# ============================================================
@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


# ============================================================
#   SCHEDULER SETUP & RUN
# ============================================================
scheduler = BackgroundScheduler()


def start_scheduler():
    # Run once every 24 hours for ALL users (extra safety)
    scheduler.add_job(check_recurring_reminders, "interval", hours=24)
    scheduler.start()


if __name__ == "__main__":
    init_db()

    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_scheduler()

