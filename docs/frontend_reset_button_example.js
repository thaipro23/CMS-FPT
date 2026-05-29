async function resetUnit(courseId, unitId) {
  const confirmed = window.confirm(
    "Bạn chắc chắn muốn làm lại Unit này? Bài làm hiện tại sẽ bị xóa và bộ câu hỏi sẽ được random lại."
  );

  if (!confirmed) {
    return;
  }

  const csrfToken = document.cookie
    .split("; ")
    .find((row) => row.startsWith("csrftoken="))
    ?.split("=")[1];

  const response = await fetch("/openedx_unit_reset/api/reset-unit/", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken,
    },
    body: JSON.stringify({
      course_id: courseId,
      unit_id: unitId,
    }),
  });

  const data = await response.json();

  if (!response.ok) {
    if (data.error_code === "cooldown_not_expired") {
      alert(`Bạn cần chờ thêm ${data.remaining_seconds} giây trước khi làm lại.`);
      return;
    }

    alert(data.message || "Không thể reset bài làm.");
    return;
  }

  window.location.reload();
}
