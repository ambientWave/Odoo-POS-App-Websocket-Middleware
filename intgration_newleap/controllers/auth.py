# -*- coding: utf-8 -*-
# Copyright 2025 Mina Samir Wahib Gebrail (https://github.com/ambientWave)
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

import os
import json
import jwt
import secrets
import logging
from datetime import datetime, timezone
from time import sleep
from odoo import http
from odoo.http import request
import werkzeug

_logger = logging.getLogger(__name__)

# JWT Authentication Configuration
AUTH_CONFIG = {
    "ACCESS_TOKEN_EXPIRY": 3600,  # 1 hour
    "REFRESH_TOKEN_EXPIRY": 7257600  # 3 months
}


def load_jwt_secret():
    """Load or create JWT secret from .env file"""
    current_directory = os.path.dirname(os.path.abspath(__file__))
    env_file_path = os.path.join(current_directory, '.env')

    if os.path.isfile(env_file_path):
        _logger.info(".env file exists.")
        with open(env_file_path, 'r') as env_file:
            lines = env_file.readlines()
            for line in lines:
                _logger.info(line.strip())
                # Ignore commented lines and empty lines
                if line.strip() and not line.startswith('#'):
                    key, value = line.strip().split('=', 1)
                    AUTH_CONFIG[key] = value
        _logger.info(AUTH_CONFIG.get('JWT_SECRET'))
    else:
        with open(env_file_path, 'w') as env_file:
            AUTH_CONFIG['JWT_SECRET'] = secrets.token_hex(32)
            env_file.write(f"JWT_SECRET={AUTH_CONFIG['JWT_SECRET']}")
            _logger.info(".env file created.")

    return AUTH_CONFIG.get('JWT_SECRET')


def authenticate_token(token, is_refresh=False):
    """
    Authenticate and validate JWT token (access or refresh token)

    Args:
        token: JWT token string
        is_refresh: Boolean indicating if this is a refresh token

    Returns:
        Dictionary with result_type, result_message, and result
    """
    current_directory = os.path.dirname(os.path.abspath(__file__))
    env_file_path = os.path.join(current_directory, '.env')

    # Load JWT secret
    if os.path.isfile(env_file_path):
        _logger.info(".env file exists.")
        with open(env_file_path, 'r') as env_file:
            lines = env_file.readlines()
            for line in lines:
                _logger.info(line.strip())
                if line.strip() and not line.startswith('#'):
                    key, value = line.strip().split('=', 1)
                    AUTH_CONFIG[key] = value
        _logger.info(AUTH_CONFIG.get('JWT_SECRET'))
    else:
        return {
            "result_type": "failure",
            "result_message": "Authentication failure",
            "result": []
        }

    secret_key = AUTH_CONFIG['JWT_SECRET']

    if not is_refresh:
        # Access token verification
        try:
            decoded_token = jwt.decode(
                token,
                secret_key,
                options={"require": ["sub", "iat", "class"], "verify_exp": False},
                algorithms=["HS512"],
            )
        except (jwt.InvalidSignatureError, jwt.MissingRequiredClaimError, jwt.InvalidTokenError) as e:
            _logger.info(e)
            return {
                "result_type": "failure",
                "result_message": "Authentication failure",
                "result": {}
            }

        is_expired = datetime.now(tz=timezone.utc).timestamp() - float(decoded_token.get("iat")) >= AUTH_CONFIG[
            'ACCESS_TOKEN_EXPIRY']

        if is_expired:
            return {
                "result_type": "failure",
                "result_message": "Authentication failure",
                "result": {}
            }

        if decoded_token.get("class") != "access":
            return {
                "result_type": "failure",
                "result_message": "Authentication failure",
                "result": {}
            }
        uid_associated_with_token = decoded_token.get("sub")
        if uid_associated_with_token:
            return {
                "result_type": "success",
                "result_message": "Authentication info",
                "result": {
                    "user_id": uid_associated_with_token
                }
            }
    else:
        # Refresh token verification
        try:
            decoded_refresh_token = jwt.decode(
                token,
                secret_key,
                options={"require": ["sub", "iat", "class"], "verify_exp": False},
                algorithms=["HS512"],
            )
        except (jwt.InvalidSignatureError, jwt.MissingRequiredClaimError, jwt.InvalidTokenError) as e:
            _logger.info(e)
            return {
                "result_type": "failure",
                "result_message": "Authentication failure",
                "result": {}
            }

        is_expired = datetime.now(tz=timezone.utc).timestamp() - float(decoded_refresh_token.get("iat")) >= AUTH_CONFIG[
            'REFRESH_TOKEN_EXPIRY']

        if is_expired:
            _logger.info(f"refresh_is_expired: {is_expired}")
            return {
                "result_type": "failure",
                "result_message": "Authentication failure",
                "result": {}
            }

        if decoded_refresh_token.get("class") != "refresh":
            return {
                "result_type": "failure",
                "result_message": "Authentication failure",
                "result": {}
            }
        try:
            uid_associated_with_token = int(decoded_refresh_token.get("sub"))
            if uid_associated_with_token:
                # Check if token is blacklisted
                request.update_env(user=uid_associated_with_token)
                request.update_context(**request.env.user.context_get())
                is_blacklisted = request.env['res.users.apikeys.jwt.refreshtoken']._is_blacklisted(
                    "read:employee write:employee read:attendance write:attendance",
                    token
                )
                '''
                One realised identified threat is that one can successuflly acquire the secret.
                Then, he can decode the token to figure out encryption algorithm and claim structure.
                Finally, he can encode as many tokens as he likes.
                He can use that to gain unauthorized token or perform DDoS.
                A way to respond to this adversary is logout all devices and immediately remove the .env file on the server so that a new file is generated.
                
                '''
                if not is_blacklisted:
                    request.env['res.users.apikeys.jwt.refreshtoken']._insert(
                        token,
                        "read:employee write:employee read:attendance write:attendance",
                        "frontend_app",
                        new_refresh_token_expiry_datetime,
                        True
                    )
                    request.env['res.users.apikeys.jwt.refreshtoken']._insert(is_blacklisted[0])
                    _logger.info(is_blacklisted)
                    return {
                        "result_type": "success",
                        "result_message": "Authentication info",
                        "result": {
                            "user_id": uid_associated_with_token
                        }
                    }
                else:
                    if is_blacklisted[1]:
                        return {
                            "result_type": "failure",
                            "result_message": "Authentication failure",
                            "result": {}
                        }
                    else:
                        request.env['res.users.apikeys.jwt.refreshtoken']._blacklist(is_blacklisted[0])
                        _logger.info(is_blacklisted)
                        return {
                            "result_type": "success",
                            "result_message": "Authentication info",
                            "result": {
                                "user_id": uid_associated_with_token
                            }
                        }
        except Exception as e:
            return {
                "result_type": "failure",
                "result_message": "Authentication failure",
                "result": {}
            }

class AuthController(http.Controller):
    """Controller for JWT authentication endpoints"""

    @http.route('/api/authenticate', type='json', auth="none",
                methods=['POST'], csrf=False, save_session=False, cors="*")
    def authenticate_user(self, **kw):
        """
        Authenticate user and generate JWT access and refresh tokens
        """
        # Get database list
        db = http.db_list(force=True)
        _logger.info(db)
        _logger.info(kw)

        if len(db) == 1:
            db = db[0]
            login = kw.get('login')
            password = kw.get('password')
            fontend_stored_token = kw.get('token')
            _logger.info(login)
            _logger.info(password)

            if (not login and not password) or not fontend_stored_token:
                # Request sent from remote - check headers
                headers = request.httprequest.headers
                fontend_stored_token = headers.get("Authorization", None)
                login = headers.get("login", None)
                password = headers.get("password", None)
                _credentials_includes_in_headers = all([db, login, password])

                if not _credentials_includes_in_headers:
                    return {
                        "result_type": "failure",
                        "result_message": "Please provide correct username and password",
                        "result": {}
                    }

            _logger.info({"db": db, "login": login, "password": password})

            try:
                uid_dict = request.session.authenticate(db, {
                    'type': 'password',
                    'login': login,
                    'password': password
                })
            except Exception:
                return {
                    "result_type": "failure",
                    "result_message": "The username and password is incorrect",
                    "result": {}
                }

            _logger.info(uid_dict)

            if uid_dict:
                # Update environment with authenticated user
                request.update_env(user=uid_dict.get('uid'))
                _logger.info("line #299")
                request.update_context(**request.env.user.context_get())
                user_id = uid_dict.get('uid')

                # Load JWT secret
                secret_key = load_jwt_secret()

                # Generate access token
                payload = {
                    "sub": str(user_id),
                    "iat": datetime.now(tz=timezone.utc).timestamp(),
                    "class": "access"
                }
                _logger.info(f"iat: {payload['iat']}")
                access_token_expiry = payload['iat'] + AUTH_CONFIG['ACCESS_TOKEN_EXPIRY']
                _logger.info("just before creating 1st jwt")
                access_token = jwt.encode(payload, secret_key, algorithm='HS512')
                _logger.info("1st jwt created.")

                # Generate refresh token
                sleep(1)
                payload['iat'] = datetime.now(tz=timezone.utc).timestamp()
                payload['class'] = "refresh"
                refresh_token = jwt.encode(payload, secret_key, algorithm='HS512')
                refresh_token_expiry = payload['iat'] + AUTH_CONFIG['REFRESH_TOKEN_EXPIRY']
                refresh_token_expiry_datetime = datetime.fromtimestamp(refresh_token_expiry)
                _logger.info(refresh_token_expiry_datetime)

                # Store refresh token
                request.env['res.users.apikeys.jwt.refreshtoken']._insert(
                    refresh_token,
                    "read:employee write:employee read:attendance write:attendance",
                    "frontend_app",
                    refresh_token_expiry_datetime
                )

                _logger.info("just before our workaround")
                return {
                    "result_type": "success",
                    "result_message": "Authentication info",
                    "result": {
                        "access_token": access_token,
                        "access_token_expiry": access_token_expiry,
                        "refresh_token": refresh_token,
                        "refresh_token_expiry": refresh_token_expiry
                    }
                }
        else:
            werkzeug.exceptions.abort(
                request.make_json_response(
                    {
                        "result_type": "failure",
                        "result_message": "Please provide a valid database name",
                        "result": {}
                    },
                    headers={"Content-Type": "application/json; charset=utf-8"},
                    status=403
                )
            )

    @http.route('/api/refresh_token', type='json', auth="none",
                methods=['POST'], csrf=False, save_session=False, cors="*")
    def create_access_refresh_token_pair(self, **kw):
        """
        Refresh access and refresh tokens using a valid refresh token
        """
        payload = request.httprequest.data.decode()
        payload = json.loads(payload)
        refresh_token = payload.get("refresh_token")

        # Validate refresh token
        check_refresh_token = authenticate_token(refresh_token, is_refresh=True)

        if check_refresh_token['result_type'] == 'failure':
            return check_refresh_token
        else:
            secret_key = AUTH_CONFIG['JWT_SECRET']
            _logger.info(check_refresh_token)

            # Generate new access token
            payload = {
                "sub": str(request.env.user.id),
                "iat": datetime.now(tz=timezone.utc).timestamp(),
                "class": "access"
            }
            _logger.info("just before creating 1st jwt")
            new_access_token = jwt.encode(payload, secret_key, algorithm='HS512')
            new_access_token_expiry = payload['iat'] + AUTH_CONFIG['ACCESS_TOKEN_EXPIRY']
            _logger.info("1st jwt created.")

            # Generate new refresh token
            sleep(1)
            payload['iat'] = datetime.now(tz=timezone.utc).timestamp()
            payload['class'] = "refresh"
            new_refresh_token = jwt.encode(payload, secret_key, algorithm='HS512')
            new_refresh_token_expiry = payload['iat'] + AUTH_CONFIG['REFRESH_TOKEN_EXPIRY']
            new_refresh_token_expiry_datetime = datetime.fromtimestamp(new_refresh_token_expiry)

            # Store refresh token
            request.env['res.users.apikeys.jwt.refreshtoken']._insert(
                new_refresh_token,
                "read:employee write:employee read:attendance write:attendance",
                "frontend_app",
                new_refresh_token_expiry_datetime
            )
            # Update response
            check_refresh_token.get('result').pop('user_id')
            check_refresh_token.get('result').update({
                "access_token": new_access_token,
                "access_token_expiry": new_access_token_expiry,
                "refresh_token": new_refresh_token,
                "refresh_token_expiry": new_refresh_token_expiry
            })

            return check_refresh_token

    @http.route('/api/refresh_device_token', methods=["POST"], csrf=False, type='json', auth="none")
    def refresh_device_token(self, **kw):
        """
        Update employee's FCM device token for push notifications
        """
        # ---------------------------
        # 1) Authenticate user by token
        # ---------------------------
        if not request.session.uid:
            headers = request.httprequest.headers
            fontend_stored_token = headers.get("Authorization", None)
            auth_result = authenticate_token(token=fontend_stored_token)
            _logger.info(auth_result)

            if auth_result.get('result_type') != 'success':
                return self._format_response("failure", "Authentication failed")

            user_id = int(auth_result.get('result', {}).get('user_id'))
            request.update_env(user=user_id)
            request.update_context(**request.env.user.context_get())

        # ---------------------------
        # 2) Read JSON payload
        # ---------------------------
        payload = json.loads(request.httprequest.data.decode())

        sig_hash = payload.get("sig_hash")
        fcm_token = payload.get("fcm_token")
        platform = payload.get("platform")

        # ---------------------------
        # 3) Validate required fields
        # ---------------------------
        missing_fields = []
        if not sig_hash:
            missing_fields.append("sig_hash")
        if not fcm_token:
            missing_fields.append("fcm_token")
        if not platform:
            missing_fields.append("platform")

        if missing_fields:
            return self._format_response(
                "failure",
                f"Missing required fields: {', '.join(missing_fields)}"
            )

        # ---------------------------
        # 4) Find employee
        # ---------------------------
        employee_object = request.env['hr.employee'].sudo().search(
            [('first_time_authentication_frontend_hash', '=', sig_hash)],
            limit=1
        )

        if not employee_object:
            return self._format_response("failure", "Invalid signature")

        # ---------------------------
        # 5) Update device token
        # ---------------------------
        employee_object.write({
            "device_token": fcm_token,
            "platform": platform,
        })

        return self._format_response("success", "Device Token Updated Successfully", {
            "device_token": fcm_token,
            "platform": platform
        })

    def _format_response(self, result_type, result_message, result_data=None):
        """Helper method to format JSON responses"""
        return {
            "result_type": result_type,
            "result_message": result_message,
            "result": result_data or []
        }
