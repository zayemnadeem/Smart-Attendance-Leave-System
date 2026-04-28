function setMessage(elementId, message, isError = false) {
    const messageElement = document.getElementById(elementId);
    if (!messageElement) return;

    messageElement.textContent = message;
    messageElement.style.color = isError ? "#b91c1c" : "#166534";
    messageElement.style.background = isError ? "#fee2e2" : "#dcfce7";
}

async function submitAttendance(event) {
    event.preventDefault();

    const statusSelect = document.getElementById("attendance-status");
    const status = statusSelect ? statusSelect.value : "";

    if (!status) {
        setMessage("attendance-message", "Please select attendance status.", true);
        return;
    }

    try {
        const response = await fetch("/mark_attendance", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ status }),
        });

        const data = await response.json();
        setMessage("attendance-message", data.message, !data.success);

        if (data.success && statusSelect) {
            statusSelect.value = "";
        }
    } catch (error) {
        setMessage("attendance-message", "Failed to connect to server.", true);
    }
}

async function updateLeaveStatus(leaveId, action) {
    const endpoint = action === "approve" ? `/approve_leave/${leaveId}` : `/reject_leave/${leaveId}`;
    const actionMessage = document.getElementById("admin-action-message");

    try {
        const response = await fetch(endpoint, { method: "POST" });
        const data = await response.json();
        setMessage("admin-action-message", data.message, !data.success);

        if (data.success) {
            const statusElement = document.getElementById(`leave-status-${leaveId}`);
            const rowElement = document.getElementById(`leave-row-${leaveId}`);

            if (statusElement) {
                const statusText = action === "approve" ? "Approved" : "Rejected";
                statusElement.textContent = statusText;
                statusElement.className = `status ${statusText.toLowerCase()}`;
            }

            if (rowElement) {
                const actionCell = rowElement.querySelector("td:last-child");
                if (actionCell) {
                    actionCell.innerHTML = '<span class="muted">No action</span>';
                }
            }
        }
    } catch (error) {
        if (actionMessage) {
            setMessage("admin-action-message", "Failed to update leave status.", true);
        }
    }
}

document.addEventListener("DOMContentLoaded", () => {
    const attendanceForm = document.getElementById("attendance-form");
    if (attendanceForm) {
        attendanceForm.addEventListener("submit", submitAttendance);
    }
});
