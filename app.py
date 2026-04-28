from datetime import datetime

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for

from models import get_connection, init_db, seed_users


app = Flask(__name__)
app.secret_key = "smart-attendance-secret-key"

# Demo mode keeps attendance open for testing at any time.
DEMO_MODE = True


def get_client_ip():
    """Return the client's most reliable IP address."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or ""


def is_allowed_ip():
    """Allow attendance only from office network or localhost."""
    client_ip = get_client_ip()
    return client_ip.startswith("192.168.") or client_ip == "127.0.0.1"


def is_within_attendance_time(now=None):
    """Return True when attendance is allowed by time policy."""
    if DEMO_MODE:
        return True
    now = now or datetime.now()
    return 9 <= now.hour < 17


def attendance_restriction_error():
    """Validate IP/time restrictions and return an error response when blocked."""
    if not is_allowed_ip():
        return (
            jsonify(
                {
                    "success": False,
                    "message": "Attendance allowed only from 192.168.x.x or localhost (127.0.0.1).",
                }
            ),
            403,
        )

    if not is_within_attendance_time():
        return (
            jsonify(
                {
                    "success": False,
                    "message": "Attendance can only be marked between 9 AM and 5 PM.",
                }
            ),
            400,
        )

    return None


def current_user():
    """Return session user details as a dict."""
    return {
        "id": session.get("user_id"),
        "name": session.get("user_name"),
        "role": session.get("role"),
    }


def require_login(role=None):
    """Validate logged-in user and optional role."""
    user = current_user()
    if not user["id"] or not user["role"]:
        return False
    if role and user["role"] != role:
        return False
    return True


@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user_id = request.form.get("user_id", "").strip()
        role = request.form.get("role", "").strip()
        if not user_id or not role:
            flash("Please select both user and role.", "error")
            return redirect(url_for("login"))

        with get_connection() as connection:
            user = connection.execute(
                "SELECT id, name, role FROM users WHERE id = ? AND role = ?;",
                (user_id, role),
            ).fetchone()

        if user is None:
            flash("Invalid user selection.", "error")
            return redirect(url_for("login"))

        session["user_id"] = user["id"]
        session["user_name"] = user["name"]
        session["role"] = user["role"]

        if user["role"] == "student":
            return redirect(url_for("student_dashboard"))
        return redirect(url_for("admin_dashboard"))

    with get_connection() as connection:
        users = connection.execute("SELECT id, name, role FROM users ORDER BY role, name;").fetchall()
    return render_template("login.html", users=users)


@app.route("/student")
def student_dashboard():
    if not require_login(role="student"):
        flash("Please login as a student.", "error")
        return redirect(url_for("login"))

    user = current_user()
    with get_connection() as connection:
        attendance_history = connection.execute(
            """
            SELECT date, status
            FROM attendance
            WHERE user_id = ?
            ORDER BY date DESC;
            """,
            (user["id"],),
        ).fetchall()
        leave_history = connection.execute(
            """
            SELECT id, date, reason, status
            FROM leave_requests
            WHERE user_id = ?
            ORDER BY date DESC, id DESC;
            """,
            (user["id"],),
        ).fetchall()

    total_attendance = len(attendance_history)
    present_count = sum(1 for item in attendance_history if item["status"] == "Present")
    attendance_percentage = (present_count / total_attendance * 100) if total_attendance else 0

    return render_template(
        "student_dashboard.html",
        user=user,
        attendance_history=attendance_history,
        leave_history=leave_history,
        total_attendance=total_attendance,
        present_count=present_count,
        attendance_percentage=attendance_percentage,
    )


@app.route("/admin")
def admin_dashboard():
    if not require_login(role="admin"):
        flash("Please login as admin.", "error")
        return redirect(url_for("login"))

    with get_connection() as connection:
        students = connection.execute(
            "SELECT id, name, role FROM users WHERE role = 'student' ORDER BY name;"
        ).fetchall()
        attendance_records = connection.execute(
            """
            SELECT attendance.id, users.name AS student_name, attendance.date, attendance.status
            FROM attendance
            JOIN users ON attendance.user_id = users.id
            ORDER BY attendance.date DESC, attendance.id DESC;
            """
        ).fetchall()
        leave_requests = connection.execute(
            """
            SELECT leave_requests.id, users.name AS student_name, leave_requests.date,
                   leave_requests.reason, leave_requests.status
            FROM leave_requests
            JOIN users ON leave_requests.user_id = users.id
            ORDER BY leave_requests.date DESC, leave_requests.id DESC;
            """
        ).fetchall()

    return render_template(
        "admin_dashboard.html",
        user=current_user(),
        students=students,
        attendance_records=attendance_records,
        leave_requests=leave_requests,
    )


@app.route("/attendance")
def attendance_page():
    if not require_login(role="student"):
        flash("Please login as a student.", "error")
        return redirect(url_for("login"))
    return render_template("attendance.html", user=current_user())


@app.route("/leave_request")
def leave_request_page():
    if not require_login(role="student"):
        flash("Please login as a student.", "error")
        return redirect(url_for("login"))
    return render_template("leave_request.html", user=current_user())


@app.route("/mark_attendance", methods=["POST"])
def mark_attendance():
    if not require_login(role="student"):
        return jsonify({"success": False, "message": "Unauthorized access."}), 401

    restriction_error = attendance_restriction_error()
    if restriction_error is not None:
        return restriction_error

    now = datetime.now()

    status = (request.get_json(silent=True) or {}).get("status", "").strip()
    if status not in {"Present", "Absent"}:
        return jsonify({"success": False, "message": "Invalid attendance status."}), 400

    today = now.strftime("%Y-%m-%d")
    user = current_user()

    with get_connection() as connection:
        existing = connection.execute(
            "SELECT id FROM attendance WHERE user_id = ? AND date = ?;",
            (user["id"], today),
        ).fetchone()
        if existing:
            return jsonify({"success": False, "message": "Attendance already marked for today."}), 409

        connection.execute(
            "INSERT INTO attendance (user_id, date, status) VALUES (?, ?, ?);",
            (user["id"], today, status),
        )
        connection.commit()

    return jsonify({"success": True, "message": "Attendance marked successfully."})


@app.route("/apply_leave", methods=["POST"])
def apply_leave():
    if not require_login(role="student"):
        flash("Please login as a student.", "error")
        return redirect(url_for("login"))

    leave_date = request.form.get("date", "").strip()
    reason = request.form.get("reason", "").strip()

    if not leave_date or not reason:
        flash("Date and reason are required to apply for leave.", "error")
        return redirect(url_for("leave_request_page"))

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO leave_requests (user_id, date, reason, status)
            VALUES (?, ?, ?, 'Pending');
            """,
            (current_user()["id"], leave_date, reason),
        )
        connection.commit()

    flash("Leave request submitted.", "success")
    return redirect(url_for("student_dashboard"))


def update_leave_status(leave_id, new_status):
    if not require_login(role="admin"):
        return jsonify({"success": False, "message": "Unauthorized access."}), 401

    with get_connection() as connection:
        leave_request = connection.execute(
            "SELECT id, status FROM leave_requests WHERE id = ?;",
            (leave_id,),
        ).fetchone()
        if leave_request is None:
            return jsonify({"success": False, "message": "Leave request not found."}), 404

        connection.execute(
            "UPDATE leave_requests SET status = ? WHERE id = ?;",
            (new_status, leave_id),
        )
        connection.commit()

    message_map = {
        "Approved": "Leave approved.",
        "Rejected": "Leave rejected.",
    }
    return jsonify({"success": True, "message": message_map.get(new_status, "Leave status updated.")})


@app.route("/approve_leave/<int:leave_id>", methods=["POST"])
def approve_leave(leave_id):
    return update_leave_status(leave_id, "Approved")


@app.route("/reject_leave/<int:leave_id>", methods=["POST"])
def reject_leave(leave_id):
    return update_leave_status(leave_id, "Rejected")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "success")
    return redirect(url_for("login"))


if __name__ == "__main__":
    init_db()
    seed_users()
    app.run(debug=True)
