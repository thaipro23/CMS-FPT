import React, { useEffect, useState } from 'react';

function getCookie(name) {
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) return parts.pop().split(';').shift();
  return '';
}

function formatWait(totalSeconds) {
  const secondsNumber = Number(totalSeconds || 0);
  const minutes = Math.floor(secondsNumber / 60);
  const seconds = secondsNumber % 60;
  if (minutes <= 0) return `${seconds} giây`;
  return `${minutes} phút ${seconds} giây`;
}

export default function ResetUnitButton({ courseId, unitUsageKey }) {
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState(null);

  useEffect(() => {
    if (!courseId || !unitUsageKey) return;
    const url = `/api/unit-reset/v1/status/?course_id=${encodeURIComponent(courseId)}&unit_usage_key=${encodeURIComponent(unitUsageKey)}`;
    fetch(url, { credentials: 'include' })
      .then(res => res.json())
      .then(data => setStatus(data))
      .catch(() => setStatus(null));
  }, [courseId, unitUsageKey]);

  const onReset = async () => {
    const ok = window.confirm(
      'Bạn có chắc muốn làm lại bài?\n\n' +
      'Toàn bộ câu hỏi hiện tại, đáp án đã chọn và điểm của lần làm này sẽ bị xóa. ' +
      'Hệ thống sẽ random lại bộ câu hỏi mới. Bạn chỉ có thể reset tiếp sau thời gian giữa các lần thử.'
    );
    if (!ok) return;

    setLoading(true);
    try {
      const response = await fetch('/api/unit-reset/v1/reset/', {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCookie('csrftoken'),
        },
        body: JSON.stringify({ course_id: courseId, unit_usage_key: unitUsageKey }),
      });

      const data = await response.json();
      if (!response.ok) {
        if (data.code === 'RESET_COOLDOWN') {
          alert(`Bạn chưa thể làm lại bài. Vui lòng chờ ${formatWait(data.wait_seconds)} nữa.`);
          setStatus(data);
          return;
        }
        alert(data.message || 'Không thể làm lại bài.');
        return;
      }

      window.location.reload();
    } finally {
      setLoading(false);
    }
  };

  const waitText = status && status.wait_seconds > 0 ? `Chờ ${formatWait(status.wait_seconds)}` : '';

  return (
    <button type="button" onClick={onReset} disabled={loading} className="btn btn-outline-primary">
      {loading ? 'Đang reset...' : waitText || 'Làm lại bài'}
    </button>
  );
}
