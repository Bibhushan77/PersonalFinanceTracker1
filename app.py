from flask import (
    Flask, render_template, redirect, url_for,
    request, session, flash
)
from datetime import date, datetime, timedelta
from functools import wraps
import calendar

from werkzeug.security import generate_password_hash, check_password_hash

from database import get_connection, init_db

# ============================================================
#   APP + SHARED CATEGORY LISTS
# ============================================================
app = Flask(__name__)
app.secret_key = "change-me-in-real-project"

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
#   RECURRING HELPER: apply any due recurring transactions
# ============================================================
def apply_due_recurring(user_id: int):
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
        tx_date = r["next_date"]
        desc = r["description"] or ""

        cur.execute("""
            INSERT INTO transactions (user_id, date, description, category, type, amount)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, tx_date, desc, r["category"], r["type"], r["amount"]))

        current = datetime.strptime(r["next_date"], "%Y-%m-%d").date()
        freq = r["frequency"]

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
                new_date = date(current.year + 1, current.month, 28)
        else:
            new_date = current + timedelta(days=30)

        cur.execute("""
            UPDATE recurring_transactions
            SET next_date = ?
            WHERE id = ?
        """, (new_date.isoformat(), r["id"]))

    conn.commit()
    conn.close()


# ============================================================
#   LANDING PAGE
# ============================================================
@app.route("/")
def root():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("auth_login"))


# ============================================================
#   DASHBOARD (UPDATED TO SHOW FIRST NAME)
# ============================================================
@app.route("/dashboard")
@login_required
def dashboard():
    user_id = current_user_id()
    apply_due_recurring(user_id)

    # NEW → fetch the user's first name
    full_name = session.get("user_name", "User")
    first_name = full_name.split()[0]

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT COALESCE(SUM(amount), 0) AS income
        FROM transactions
        WHERE user_id = ? AND type = 'Income'
    """, (user_id,))
    total_income = cur.fetchone()["income"]

    cur.execute("""
        SELECT COALESCE(SUM(amount), 0) AS expenses
        FROM transactions
        WHERE user_id = ? AND type = 'Expense'
    """, (user_id,))
    total_expenses = cur.fetchone()["expenses"]

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
        first_name=first_name,     # <--- PASSED TO HTML
        total_income=total_income,
        total_expenses=total_expenses,
        balance=balance,
        recent_transactions=recent,
    )


# ============================================================
#   TRANSACTIONS LIST
# ============================================================
@app.route("/transactions")
@login_required
def transactions():
    user_id = current_user_id()
    apply_due_recurring(user_id)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM transactions
        WHERE user_id = ?
        ORDER BY date DESC
    """, (user_id,))
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
        description = request.form.get("description") or ""
        category = request.form.get("category")
        ttype = request.form.get("type")
        amount = request.form.get("amount")

        if not (date_val and category and ttype and amount):
            flash("Please fill in all required fields.", "danger")
            return redirect(url_for("transactions"))

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO transactions (user_id, date, description, category, type, amount)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, date_val, description, category, ttype, float(amount)))
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
    cur.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (tid, user_id))
    conn.commit()
    conn.close()
    return redirect(url_for("transactions"))


# ============================================================
#   REPORTS
# ============================================================
@app.route("/reports")
@login_required
def reports():
    user_id = current_user_id()
    apply_due_recurring(user_id)

    from_date = request.args.get("fromDate") or None
    to_date = request.args.get("toDate") or None

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

    where_clause = " WHERE " + " AND ".join(conditions)

    cur.execute(
        "SELECT COALESCE(SUM(amount),0) AS total "
        "FROM transactions" + where_clause + " AND type='Income'",
        params
    )
    total_income = cur.fetchone()["total"]

    cur.execute(
        "SELECT COALESCE(SUM(amount),0) AS total "
        "FROM transactions" + where_clause + " AND type='Expense'",
        params
    )
    total_expenses = cur.fetchone()["total"]

    balance = total_income - total_expenses

    cur.execute(
        "SELECT category, SUM(amount) AS total "
        "FROM transactions" + where_clause +
        " AND type='Expense' GROUP BY category ORDER BY total DESC",
        params
    )
    expense_breakdown = cur.fetchall()

    cur.execute(
        "SELECT category, amount FROM budgets WHERE user_id = ?",
        (user_id,)
    )
    budgets_raw = cur.fetchall()
    budgets_map = {row["category"]: row["amount"] for row in budgets_raw}

    cur.execute(
        """
        SELECT strftime('%Y-%m', date) AS ym,
               SUM(CASE WHEN type='Income' THEN amount ELSE 0 END) AS income_total,
               SUM(CASE WHEN type='Expense' THEN amount ELSE 0 END) AS expense_total
        FROM transactions
        """ + where_clause + """
        GROUP BY ym
        ORDER BY ym
        """,
        params
    )
    rows = cur.fetchall()
    month_labels = [row["ym"] for row in rows]
    month_income = [row["income_total"] for row in rows]
    month_expense = [row["expense_total"] for row in rows]

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
    import csv
    from io import StringIO
    from flask import make_response

    user_id = current_user_id()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT date, description, category, type, amount
        FROM transactions
        WHERE user_id = ?
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
    from flask import make_response

    user_id = current_user_id()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT date, description, category, type, amount
        FROM transactions
        WHERE user_id = ?
        ORDER BY date DESC
    """, (user_id,))
    rows = cur.fetchall()
    conn.close()

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    pdf.cell(200, 10, txt="Financial Report", ln=1, align="C")
    pdf.ln(5)

    for r in rows:
        line = f"{r['date']} - {r['category']} - {r['type']} - ${r['amount']}"
        pdf.cell(0, 8, txt=line, ln=1)

    response = make_response(pdf.output(dest="S").encode("latin-1"))
    response.headers["Content-Disposition"] = "attachment; filename=report.pdf"
    response.headers["Content-Type"] = "application/pdf"
    return response


# ============================================================
#   BUDGETS
# ============================================================
@app.route("/budgets", methods=["GET", "POST"])
@login_required
def manage_budgets():
    user_id = current_user_id()
    conn = get_connection()
    cur = conn.cursor()

    if request.method == "POST":
        category = request.form.get("category", "").strip()
        amount_raw = request.form.get("amount", "").strip()

        if category and amount_raw:
            try:
                amount = float(amount_raw)
                cur.execute("""
                    INSERT INTO budgets (user_id, category, amount)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id, category)
                    DO UPDATE SET amount = excluded.amount
                """, (user_id, category, amount))
                conn.commit()
            except ValueError:
                flash("Budget amount must be a number.", "danger")

        conn.close()
        return redirect(url_for("manage_budgets"))

    edit_budget = None
    edit_id = request.args.get("edit_id")

    if edit_id:
        cur.execute(
            "SELECT id, category, amount FROM budgets WHERE id = ? AND user_id = ?",
            (edit_id, user_id)
        )
        edit_budget = cur.fetchone()

    cur.execute(
        "SELECT id, category, amount FROM budgets WHERE user_id = ? ORDER BY category",
        (user_id,)
    )
    rows = cur.fetchall()

    conn.close()

    return render_template(
        "budgets.html",
        budgets=rows,
        expense_categories=EXPENSE_CATEGORIES,
        edit_budget=edit_budget
    )


@app.route("/budgets/delete/<int:bid>", methods=["POST"])
@login_required
def delete_budget(bid):
    user_id = current_user_id()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM budgets WHERE id = ? AND user_id = ?", (bid, user_id))
    conn.commit()
    conn.close()
    return redirect(url_for("manage_budgets"))


# ============================================================
#   RECURRING (unchanged from your working version)
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
                        (user_id, description, category, type, amount,
                         frequency, next_date, reminder_type, active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """, (user_id, description, category, r_type, amount,
                      frequency, next_date, reminder_type))
                conn.commit()
            except ValueError:
                flash("Amount must be a number.", "danger")

        conn.close()
        return redirect(url_for("recurring"))

    conn.close()
    apply_due_recurring(user_id)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM recurring_transactions
        WHERE user_id = ?
        ORDER BY next_date
    """, (user_id,))
    recurring_items = cur.fetchall()
    conn.close()

    return render_template(
        "recurring.html",
        recurring_items=recurring_items,
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
        "DELETE FROM recurring_transactions WHERE id = ? AND user_id = ?",
        (rid, user_id)
    )
    conn.commit()
    conn.close()
    return redirect(url_for("recurring"))


# ============================================================
#   AUTH (UPDATED TO STORE user_name)
# ============================================================
@app.route("/auth/login", methods=["GET", "POST"])
def auth_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_connection()
        cur = conn.cursor()

        cur.execute("SELECT * FROM users WHERE email = ?", (email,))
        user = cur.fetchone()

        conn.close()

        # USER NOT FOUND
        if not user:
            flash("User not found. Please check your email.", "danger")
            return redirect(url_for("auth_login"))

        # WRONG PASSWORD
        if not check_password_hash(user["password_hash"], password):
            flash("Incorrect password. Please try again.", "danger")
            return redirect(url_for("auth_login"))

        # SUCCESS
        session["user_id"] = user["id"]
        session["user_name"] = user["name"]
        return redirect(url_for("dashboard"))

    return render_template("auth_login.html")



@app.route("/auth/register", methods=["GET", "POST"])
def auth_register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not (name and email and password):
            flash("All fields are required.", "danger")
            return redirect(url_for("auth_register"))

        pw_hash = generate_password_hash(password)

        conn = get_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO users (name, email, password_hash)
                VALUES (?, ?, ?)
            """, (name, email, pw_hash))
            conn.commit()
        except Exception:
            conn.close()
            flash("That email is already registered.", "danger")
            return redirect(url_for("auth_register"))

        conn.close()
        flash("Account created. Please log in.", "success")
        return redirect(url_for("auth_login"))

    return render_template("auth_register.html")


@app.route("/auth/forgot", methods=["GET", "POST"])
def auth_forgot():
    return render_template("auth_forgot.html")


@app.route("/auth/logout")
def auth_logout():
    session.clear()
    return redirect(url_for("auth_login"))


# ============================================================
#   ERROR HANDLER
# ============================================================
@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


# ============================================================
#   START APP
# ============================================================
if __name__ == "__main__":
    init_db()
    app.run(debug=True)
