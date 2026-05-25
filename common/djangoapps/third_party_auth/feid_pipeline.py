import logging
from django.contrib.auth import get_user_model, login
from django.http import HttpResponseRedirect

logger = logging.getLogger(__name__)

def associate_by_username_only(strategy, details, backend, user=None, *args, **kwargs):
    """
    FEID auto-login mà không cần tạo UserSocialAuth.
    Dừng pipeline link trực tiếp đến auth_user.
    """
    try:
        # Nếu pipeline đã có user → bỏ qua
        if user:
            logger.debug("[FEID AUTO-LINK] User đã tồn tại trong pipeline.")
            return {"user": user}

        try:
            log_payload = {
                'username': details.get('username'),
                'email': details.get('email'),
            }
            logger.info(f"[FEID AUTO-LINK] Incoming details: {log_payload}")
        except Exception:
            logger.exception("[FEID AUTO-LINK] Failed to log incoming details")

        username = details.get("username")
        email = details.get("email")
        if not username and not email:
            logger.warning("[FEID AUTO-LINK] Không có username hoặc email từ FEID, bỏ qua auto-link. details=%s", details)
            return {}

        User = get_user_model()
        matched_user = None

        # Thử tìm user bằng username trước
        if username:
            matched_user = User.objects.filter(username=username).first()
            if matched_user:
                logger.info(f"[FEID AUTO-LINK] Tìm thấy user bằng username='{username}', user_id={matched_user.pk}")
            else:
                logger.info(f"[FEID AUTO-LINK] Không tìm thấy user bằng username='{username}', thử email nếu có.")

        # Nếu không tìm thấy bằng username, thử bằng email
        if not matched_user and email:
            matched_user = User.objects.filter(email=email).first()
            if matched_user:
                logger.info(f"[FEID AUTO-LINK] Tìm thấy user bằng email='{email}', user_id={matched_user.pk}")
            else:
                logger.info(f"[FEID AUTO-LINK] Không tìm thấy user bằng email='{email}', details={details}")

        if not matched_user:
            return {}

        # Đăng nhập trực tiếp
        request = getattr(strategy, "request", None)
        if request is None:
            logger.warning("[FEID AUTO-LINK] Không có request trong strategy, không thể auto-login.")
            return {"user": matched_user, "is_new": False}

        matched_user.backend = "django.contrib.auth.backends.ModelBackend"
        login(request, matched_user)
        logger.info("[FEID AUTO-LINK] Đã đăng nhập trực tiếp user=%s (matched_user=%s)", matched_user.username, matched_user.pk)

        # Redirect đến trang dashboard
        redirect_url = "/dashboard"  
        response = HttpResponseRedirect(redirect_url)
        return response

    except Exception as e:
        logger.exception(f"[FEID AUTO-LINK] Lỗi trong quá trình auto-login: {e}")
        return {}
