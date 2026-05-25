# common/djangoapps/third_party_auth/feid_backend.py
import hashlib
import base64
import os
import logging
import json
import requests
from urllib.parse import urlencode, quote
from social_core.backends.oauth import BaseOAuth2
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)


class FEIDOAuth2(BaseOAuth2):
    """
    Custom OAuth2 backend for FEID (FPT Education Identity)
    Hỗ trợ chuẩn OAuth2 + PKCE (code_verifier / code_challenge)
    Dành cho tích hợp SSO với hệ thống EDX
    """
    name = 'feid'
    BASE_URL = 'https://feid.fpt.edu.vn'
    AUTHORIZATION_URL = f'{BASE_URL}/Account/Login'
    TOKEN_URL = f'{BASE_URL}/connect/token'
    USER_DATA_URL = f'{BASE_URL}/connect/userinfo'

    ACCESS_TOKEN_METHOD = 'POST'
    SCOPE_SEPARATOR = ' '
    EXTRA_DATA = [('refresh_token', 'refresh_token')]

    # -------------------------------------------------------------------------
    # PKCE utilities
    # -------------------------------------------------------------------------
    def _generate_pkce(self):
        """
        Sinh ra code_verifier và code_challenge cho PKCE
        """
        code_verifier = base64.urlsafe_b64encode(os.urandom(40)).rstrip(b'=').decode('utf-8')
        digest = hashlib.sha256(code_verifier.encode('utf-8')).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode('utf-8')
        return code_verifier, code_challenge

    # -------------------------------------------------------------------------
    # Redirect user to FEID (Login page)
    # -------------------------------------------------------------------------
    def auth_params(self, state=None):
        """
        FEID
        """
        redirect_uri = self.get_redirect_uri()
        state = state or self.get_or_create_state()

        code_verifier, code_challenge = self._generate_pkce()
        self.strategy.session_set('feid_code_verifier', code_verifier)
        nonce = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b'=').decode('utf-8')

        logger.info("[FEID] Generated PKCE code_verifier and saved to session.")

        inner_query = {
            'response_type': 'code',
            'redirect_uri': redirect_uri,
            'client_id': self.setting('KEY'),
            'scope': 'openid profile email offline_access',
            'nonce': nonce,
            'state': state,
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256',
        }

        return_url = '/connect/authorize/callback?' + urlencode(inner_query, quote_via=quote)
        return {'ReturnUrl': return_url}

    def auth_url(self):
        """
        FEID yêu cầu query param dạng ?ReturnUrl=<encoded_url>
        """
        params = self.auth_params()

        # Ghi vào session để pipeline parse_query_params() không lỗi
        self.strategy.session_set('auth_entry', 'login')

        logger.info("[FEID] Set auth_entry=login to session for pipeline.")
        return f"{self.AUTHORIZATION_URL}?{urlencode(params, quote_via=quote)}"

    def auth_complete_params(self, state=None):
        """
        Tham số POST tới /connect/token
        """
        data = {
            'grant_type': 'authorization_code',
            'client_id': self.setting('KEY'),
            'code': self.data.get('code'),
            'redirect_uri': self.get_redirect_uri(),
        }

        # Get code_verifier FROM session
        code_verifier = self.strategy.session_get('feid_code_verifier')
        #logger.info(f"[FEID] Retrieved code_verifier from session: {'FOUND' if code_verifier else 'MISSING'}")

        if code_verifier:
            data['code_verifier'] = code_verifier

        return data

    def request_access_token(self, *args, **kwargs):
        """
        Gửi request POST /connect/token theo format FEID yêu cầu
        """
        token_url = self.TOKEN_URL
        data = self.auth_complete_params()
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}

        #logger.info(f"[FEID] POST /connect/token with params: {data}")
        response = requests.post(token_url, data=urlencode(data), headers=headers)

        # FEID return 400 when miss code_verifier 
        response.raise_for_status()
        return response.json()

    def validate_state(self):
        data = self.strategy.request_data()
        provided = data.get('state')
        logger.warning(f"[FEID TEST MODE] Skipping state validation. provided={provided}")
        return provided or 'dummy_state'

    def get_user_details(self, response):
        """
        Chuẩn hóa thông tin user lấy từ FEID về EDX.
        Lấy duy nhất giá trị RollNumber (nếu có) từ projectCampuses và gán làm `username`.
        Nếu không có RollNumber hợp lệ, `username` sẽ là `None`.
        """
        # Only use RollNumber from projectCampuses as username
        email = response.get('email')
        project_campuses = response.get('projectCampuses') or []
        if isinstance(project_campuses, str):
            try:
                parsed = json.loads(project_campuses)
                if isinstance(parsed, list):
                    project_campuses = parsed
                else:
                    # unexpected shape -> ignore
                    project_campuses = []
            except Exception:
                logger.debug("[FEID] Failed to parse projectCampuses JSON string; ignoring")
                project_campuses = []

        rollnumber = None
        for pc in project_campuses:
            rn = None
            if isinstance(pc, dict):
                rn = pc.get('RollNumber')
            elif isinstance(pc, str):
                # element could be a JSON string representing an object
                try:
                    parsed = json.loads(pc)
                    if isinstance(parsed, dict):
                        rn = parsed.get('RollNumber')
                except Exception:
                    # ignore plain strings
                    pass

            if rn:
                rollnumber = str(rn).strip()
                logger.debug(f"[FEID] Using RollNumber: {rollnumber}")
                break

        normalized_username = rollnumber if rollnumber else None

        details = {
            'username': normalized_username,
            'email': email,
        }

        #logger.info(f"[FEID] Normalized user details for pipeline (rollonly): {details}")
        return details


    def user_data(self, access_token, *args, **kwargs):
        logger = logging.getLogger(__name__)
        try:
            response = self.get_json(
                self.USER_DATA_URL,
                headers={'Authorization': f'Bearer {access_token}'}
            )
            #logger.info(f"[FEID] User info response: {response}")
            return response
        except Exception as e:
            logger.exception(f"[FEID] Failed to fetch user info: {e}")
            raise

    
    
    
    

