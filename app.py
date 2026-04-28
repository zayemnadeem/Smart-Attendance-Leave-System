from datetime import datetime

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for

from models import get_connection, init_db, seed_subjects, seed_users


app = Flask(__name__)
app.secret_key = "smart-attendance-secret-key"

# Demo mode keeps attendance open for testing at any time.
DEMO_MODE = True

TIMETABLE = {
    "Monday": {
        1: "GED 3201",
        2: "GED 3201",
        3: "CSD 3255",
        4: "CSD 3181",
        5: "CSDX 623",
        6: "MSD 3181",
        7: "SEM",
    },
    "Tuesday": {
        1: "CSD 3252",
        2: "CSD 3254",
        3: "GEDX 209",
        4: "GEDX 112",
        5: "CSDX 623",
        6: "CSD 3253",
        7: "CSD 3181",
    },
    "Wednesday": {
        1: "CSD 3253",
        2: "CSD 3255",
        3: "CSDX 631",
        4: "CSD 3251",
        5: "CSDX 623",
        6: "CSD 3251",
        7: "SEM",
    },
    "Thursday": {
        1: "CSDX 631",
        2: "CSD 3252",
        3: "SSDX 11",
        4: "SSDX 13",
        5: "GEDX 209",
        6: "GEDX 112",
        7: "CSDX 625",
    },
    "Friday": {
        1: "CSD 3252",
        2: "MSD 3181",
        3: "CSD 3252",
        4: "CSD 3254",
        5: "CSDX 631",
        6: "CSD 3251",
        7: "CSDX 623",
    },
}


def bootstrap_app_data():
    """Initialize schema and seed static records required by timetable logic."""
    init_db()
    seed_users()
    all_subjects = [subject for day_map in TIMETABLE.values() for subject in day_map.values()]
    seed_subjects(all_subjects)


bootstrap_app_data()


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


def get_day_name(date_string):
    """Return week day name for YYYY-MM-DD dates."""
    return datetime.strptime(date_string, "%Y-%m-%d").strftime("%A")


def get_day_schedule(date_string):
    """Return period-to-subject mapping for the given date."""
    return TIMETABLE.get(get_day_name(date_string), {})


def get_subject_id_map(connection):
    """Return a lookup of subject name to subject id."""
    rows = connection.execute("SELECT id, name FROM subjects;").fetchall()
    return {row["name"]: row["id"] for row in rows}


def build_period_rows(connection, user_id, date_string):
    """Build 7 period rows with subject names and already marked statuses."""
    day_schedule = get_day_schedule(date_string)
    marked_rows = connection.execute(
        """
        SELECT attendance.period, attendance.status, subjects.name AS subject_name
        FROM attendance
        JOIN subjects ON attendance.subject_id = subjects.id
        WHERE attendance.user_id = ? AND attendance.date = ?;
        """,
        (user_id, date_string),
    ).fetchall()
    marked_map = {row["period"]: row for row in marked_rows}

    period_rows = []
    for period in range(1, 8):
        marked = marked_map.get(period)
        period_rows.append(
            {
                "period": period,
                "subject_name": day_schedule.get(period, "No Class Scheduled"),
                "status": marked["status"] if marked else "",
                "is_locked": marked is not None,
            }
        )
    return period_rows


def save_period_attendance(connection, user_id, date_string, period_entries):
    """Insert multiple period attendance records with duplicate checks."""
    day_schedule = get_day_schedule(date_string)
    if not day_schedule:
        return False, "No timetable configured for today."

    subject_map = get_subject_id_map(connection)
    valid_statuses = {"Present", "Absent"}
    normalized_entries = []

    for entry in period_entries:
        period = entry.get("period")
        status = (entry.get("status") or "").strip()
        if not isinstance(period, int) or period < 1 or period > 7:
            return False, "Invalid period provided."
        if period not in day_schedule:
            return False, f"No class configured for period {period}."
        if status not in valid_statuses:
            return False, f"Invalid status for period {period}."
        normalized_entries.append((period, status))

    for period, _ in normalized_entries:
        existing = connection.execute(
            "SELECT id FROM attendance WHERE user_id = ? AND date = ? AND period = ?;",
            (user_id, date_string, period),
        ).fetchone()
        if existing:
            return False, f"Attendance already marked for period {period}."

    for period, status in normalized_entries:
        subject_name = day_schedule[period]
        subject_id = subject_map.get(subject_name)
        if not subject_id:
            return False, f"Subject mapping not found for {subject_name}."
        connection.execute(
            """
            INSERT INTO attendance (user_id, date, period, subject_id, status)
            VALUES (?, ?, ?, ?, ?);
            """,
            (user_id, date_string, period, subject_id, status),
        )
    connection.commit()
    return True, "Attendance marked successfully."


def mark_excused_day(connection, user_id, date_string):
    """Mark all day periods as Excused when full-day leave is approved."""
    day_schedule = get_day_schedule(date_string)
    if not day_schedule:
        return

    subject_map = get_subject_id_map(connection)
    for period in range(1, 8):
        subject_name = day_schedule.get(period)
        if not subject_name:
            continue
        subject_id = subject_map.get(subject_name)
        if not subject_id:
            continue
        connection.execute(
            """
            INSERT INTO attendance (user_id, date, period, subject_id, status)
            VALUES (?, ?, ?, ?, 'Excused')
            ON CONFLICT(user_id, date, period)
            DO UPDATE SET status = 'Excused', subject_id = excluded.subject_id;
            """,
            (user_id, date_string, period, subject_id),
        )
    connection.commit()


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
    today = datetime.now().strftime("%Y-%m-%d")
    with get_connection() as connection:
        attendance_history = connection.execute(
            """
            SELECT attendance.date, attendance.period, subjects.name AS subject_name, attendance.status
            FROM attendance
            JOIN subjects ON attendance.subject_id = subjects.id
            WHERE attendance.user_id = ?
            ORDER BY attendance.date DESC, attendance.period ASC;
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
        today_present = connection.execute(
            """
            SELECT COUNT(*) AS present_count
            FROM attendance
            WHERE user_id = ? AND date = ? AND status = 'Present';
            """,
            (user["id"], today),
        ).fetchone()["present_count"]

        percentage_row = connection.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'Present' THEN 1 ELSE 0 END) AS present_count,
                SUM(CASE WHEN status IN ('Present', 'Absent') THEN 1 ELSE 0 END) AS total_count
            FROM attendance
            WHERE user_id = ?;
            """,
            (user["id"],),
        ).fetchone()

        subject_stats = connection.execute(
            """
            SELECT
                subjects.name AS subject_name,
                SUM(CASE WHEN attendance.status = 'Present' THEN 1 ELSE 0 END) AS attended_count,
                SUM(CASE WHEN attendance.status IN ('Present', 'Absent') THEN 1 ELSE 0 END) AS total_count
            FROM attendance
            JOIN subjects ON attendance.subject_id = subjects.id
            WHERE attendance.user_id = ?
            GROUP BY subjects.id, subjects.name
            ORDER BY subjects.name;
            """,
            (user["id"],),
        ).fetchall()

    present_count = percentage_row["present_count"] or 0
    total_classes = percentage_row["total_count"] or 0
    attendance_percentage = (present_count / total_classes * 100) if total_classes else 0

    subject_stats_with_percentage = []
    for row in subject_stats:
        total = row["total_count"] or 0
        attended = row["attended_count"] or 0
        percentage = (attended / total * 100) if total else 0
        subject_stats_with_percentage.append(
            {
                "subject_name": row["subject_name"],
                "attended_count": attended,
                "total_count": total,
                "percentage": percentage,
                "is_low": total > 0 and percentage < 75,
            }
        )

    return render_template(
        "student_dashboard.html",
        user=user,
        attendance_history=attendance_history,
        leave_history=leave_history,
        today_present=today_present,
        today_total=7,
        total_classes=total_classes,
        present_count=present_count,
        attendance_percentage=attendance_percentage,
        subject_stats=subject_stats_with_percentage,
    )


@app.route("/admin")
def admin_dashboard():
    if not require_login(role="admin"):
        flash("Please login as admin.", "error")
        return redirect(url_for("login"))

    today = datetime.now().strftime("%Y-%m-%d")
    with get_connection() as connection:
        students = connection.execute(
            "SELECT id, name, role FROM users WHERE role = 'student' ORDER BY name;"
        ).fetchall()
        attendance_records = connection.execute(
            """
            SELECT attendance.id, users.name AS student_name, attendance.date, attendance.period,
                   subjects.name AS subject_name, attendance.status
            FROM attendance
            JOIN users ON attendance.user_id = users.id
            JOIN subjects ON attendance.subject_id = subjects.id
            ORDER BY attendance.date DESC, attendance.period ASC, attendance.id DESC;
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
        subject_stats = connection.execute(
            """
            SELECT
                subjects.name AS subject_name,
                SUM(CASE WHEN attendance.status = 'Present' THEN 1 ELSE 0 END) AS attended_count,
                SUM(CASE WHEN attendance.status IN ('Present', 'Absent') THEN 1 ELSE 0 END) AS total_count
            FROM attendance
            JOIN subjects ON attendance.subject_id = subjects.id
            GROUP BY subjects.id, subjects.name
            ORDER BY subjects.name;
            """
        ).fetchall()
        daily_summary = connection.execute(
            """
            SELECT
                users.name AS student_name,
                SUM(CASE WHEN attendance.status = 'Present' THEN 1 ELSE 0 END) AS attended_count
            FROM users
            LEFT JOIN attendance
              ON attendance.user_id = users.id
             AND attendance.date = ?
            WHERE users.role = 'student'
            GROUP BY users.id, users.name
            ORDER BY users.name;
            """,
            (today,),
        ).fetchall()

    subject_stats_with_percentage = []
    for row in subject_stats:
        total = row["total_count"] or 0
        attended = row["attended_count"] or 0
        percentage = (attended / total * 100) if total else 0
        subject_stats_with_percentage.append(
            {
                "subject_name": row["subject_name"],
                "attended_count": attended,
                "total_count": total,
                "percentage": percentage,
            }
        )

    return render_template(
        "admin_dashboard.html",
        user=current_user(),
        students=students,
        attendance_records=attendance_records,
        leave_requests=leave_requests,
        subject_stats=subject_stats_with_percentage,
        daily_summary=daily_summary,
        today=today,
    )


@app.route("/attendance")
def attendance_page():
    if not require_login(role="student"):
        flash("Please login as a student.", "error")
        return redirect(url_for("login"))

    user = current_user()
    today = datetime.now().strftime("%Y-%m-%d")
    with get_connection() as connection:
        period_rows = build_period_rows(connection, user["id"], today)

    return render_template(
        "attendance.html",
        user=user,
        today=today,
        day_name=get_day_name(today),
        period_rows=period_rows,
        timetable_exists=bool(get_day_schedule(today)),
    )


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

    payload = request.get_json(silent=True) or {}
    entries = payload.get("attendance", [])
    if not entries:
        # Backward compatibility for old single-status clients.
        old_status = (payload.get("status") or "").strip()
        old_period = payload.get("period", 1)
        entries = [{"period": old_period, "status": old_status}]

    today = datetime.now().strftime("%Y-%m-%d")
    user = current_user()
    with get_connection() as connection:
        success, message = save_period_attendance(connection, user["id"], today, entries)

    if not success:
        status_code = 409 if "already marked" in message.lower() else 400
        return jsonify({"success": False, "message": message}), status_code
    return jsonify({"success": True, "message": message})


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
            "SELECT id, user_id, date, status FROM leave_requests WHERE id = ?;",
            (leave_id,),
        ).fetchone()
        if leave_request is None:
            return jsonify({"success": False, "message": "Leave request not found."}), 404

        connection.execute(
            "UPDATE leave_requests SET status = ? WHERE id = ?;",
            (new_status, leave_id),
        )
        if new_status == "Approved":
            mark_excused_day(connection, leave_request["user_id"], leave_request["date"])
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
    bootstrap_app_data()
    app.run(debug=True)
