from flask import Flask, render_template, redirect, url_for, request

app = Flask(__name__)

# ---------- LANDING: go to login first ----------
@app.route("/")
def root():
    # Always show login first
    return redirect(url_for("auth_login"))

# ---------- PAGES ----------
@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")

@app.route("/transactions")
def transactions():
    return render_template("transactions.html")

@app.route("/reports")
def reports():
    return render_template("reports.html")

@app.route("/recurring")
def recurring():
    return render_template("recurring.html")

# ---------- AUTH (UI-only demo) ----------
@app.route("/auth/login", methods=["GET", "POST"])
def auth_login():
    # For now: just redirect to dashboard on POST (no real auth yet)
    if request.method == "POST":
        return redirect(url_for("dashboard"))
    return render_template("auth_login.html")

@app.route("/auth/forgot", methods=["GET", "POST"])
def auth_forgot():
    return render_template("auth_forgot.html")

@app.route("/auth/logout")
def auth_logout():
    # In a real app you'd clear session; for now just go back to login
    return redirect(url_for("auth_login"))

# ---------- ERRORS ----------
@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404

if __name__ == "__main__":
    app.run(debug=True)
